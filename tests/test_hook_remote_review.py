"""Security-review findings on the remote-mode hooks, one test per finding.

The encoder is shared by the MCP handlers on the event loop and the hook routes'
worker threads; a lazy first load must happen once and an encode never overlap
another. Tool-input text that crosses the network is capped; the rules cache
never follows a planted link; a session id never escapes the state directory;
a rule from the wire is whole and typed or it is no rule.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from gingugu import hook_remote_flows, prompt_hook
from gingugu.embeddings import FastEmbedProvider
from tests.hook_remote_fixtures import URL, WIRE


class _SlowModel:
    built = 0
    active = 0
    overlapped = False

    def __init__(self, model_name: str) -> None:
        type(self).built += 1
        time.sleep(0.05)

    def embed(self, texts):
        cls = type(self)
        cls.active += 1
        if cls.active > 1:
            cls.overlapped = True
        time.sleep(0.01)
        cls.active -= 1
        return iter([[0.1, 0.2] for _ in texts])


@pytest.fixture
def slow_model(monkeypatch):
    import fastembed

    _SlowModel.built, _SlowModel.active, _SlowModel.overlapped = 0, 0, False
    monkeypatch.setattr(fastembed, "TextEmbedding", _SlowModel)
    monkeypatch.setattr(FastEmbedProvider, "_load_tokenizer", lambda self: None)
    return _SlowModel


def test_the_encoder_loads_once_and_never_runs_two_encodes_at_once(slow_model):
    provider = FastEmbedProvider()
    results: list = []
    threads = [
        threading.Thread(target=lambda: results.append(provider.encode("x"))) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert slow_model.built == 1
    assert not slow_model.overlapped
    assert results == [[0.1, 0.2]] * 8
    assert provider.dim == 2


def test_trip_text_is_capped_before_it_leaves(remote):
    _, install = remote
    brain = install({"/hook/trip": {"ok": True}})
    target = hook_remote_flows.RemoteTarget(URL, "env")
    hook_remote_flows.report_trip(target, "s", "x" * 50_000, ["m"], ["crow"])
    assert len(brain.calls[0][1]["text"]) == hook_remote_flows.TRIP_TEXT_CHARS == 2000


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
def test_the_rules_cache_never_follows_a_planted_link(remote, tmp_path):
    _, install = remote
    install({"/hook/tripwires": {"tripwires": [WIRE]}})
    state = tmp_path / "state"
    state.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    os.chmod(victim, 0o644)
    target = hook_remote_flows.RemoteTarget(URL, "env")
    link = hook_remote_flows._cache_path(state, URL, ["crow"])
    link.symlink_to(victim)
    rules = hook_remote_flows.remote_rules(target, ["crow"], state)
    assert [r.memory_id for r in rules] == ["m-merge"]  # still served from the brain
    assert victim.read_text() == "keep me"
    assert victim.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("field", ["tool_pattern", "input_pattern", "title", "memory_id"])
def test_a_rule_with_a_mistyped_field_is_no_rule(field):
    assert hook_remote_flows._parse([{**WIRE, field: 7}]) is None


@pytest.mark.parametrize("session", ["../../escape", "a/b", "..", "x" * 300])
def test_a_hostile_session_id_stays_in_the_prompt_state_dir(tmp_path, session):
    db = tmp_path / "data" / "memories.db"
    prompt_hook.save_suppressed(db, session, {"m1"})
    assert prompt_hook.load_suppressed(db, session) == {"m1"}
    written = [p for p in tmp_path.rglob("*.json")]
    assert written and all(p.parent == db.parent / "hook-sessions" for p in written)
