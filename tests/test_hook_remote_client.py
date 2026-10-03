"""The three hooks in remote mode, seen from the client.

With a remote brain on, a hook never opens the local DB: recall and warm-up are
asked of the brain, tripwire rules are fetched from it (cached a minute, the
last copy kept for when the brain is away) and matched here. Per-session
suppression stays in this machine's hook-sessions files and travels with each
request, so the brain holds no session state. A brain that cannot answer means
a quiet hook - never a fall back to a local copy.
"""

from __future__ import annotations

import json
import sys

import pytest

from gingugu import prompt_hook, tool_hook
from gingugu.database import Database
from tests.hook_remote_fixtures import PROMPT, WIRE, _no_local_encoder, _prompt, _tool

# --- recall ------------------------------------------------------------------


def test_recall_asks_the_brain_and_never_opens_the_local_db(remote, capsys, monkeypatch):
    db, install = remote
    brain = install({"/hook/recall": {"context": "CTX-FROM-PI", "ids": ["m1"]}})
    monkeypatch.setattr("gingugu.embeddings.build_provider", _no_local_encoder)
    assert prompt_hook.run(_prompt()) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["additionalContext"] == "CTX-FROM-PI"
    ((path, body),) = brain.calls
    assert path == "/hook/recall"
    assert body["prompt"] == PROMPT
    assert body["namespaces"] == ["crow", "gingugu"]
    assert body["session_id"] == "cc-1"
    assert body["suppressed"] == []
    assert set(body["config"]) == {"bar", "margin", "cap", "require_lexical"}
    assert not db.exists()


def test_recall_suppression_is_kept_here_and_sent_up(remote, capsys):
    _, install = remote
    brain = install({"/hook/recall": {"context": "CTX", "ids": ["m1", "m2"]}})
    prompt_hook.run(_prompt())
    prompt_hook.run(_prompt())
    assert sorted(brain.calls[1][1]["suppressed"]) == ["m1", "m2"]
    other = install({"/hook/recall": {"context": None, "ids": []}})
    prompt_hook.run(_prompt(session="cc-2"))
    assert other.calls[0][1]["suppressed"] == []


def test_recall_persona_rides_along(remote, monkeypatch):
    _, install = remote
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    brain = install({"/hook/recall": {"context": None, "ids": []}})
    prompt_hook.run(_prompt())
    assert brain.calls[0][1]["namespaces"] == ["crow", "beepboop", "gingugu"]


def test_a_short_prompt_never_reaches_the_network(remote):
    _, install = remote
    brain = install({})
    prompt_hook.run(_prompt(prompt="yup"))
    assert brain.calls == []


@pytest.mark.parametrize("reply", [None, {"context": None, "ids": []}, {"bogus": 1}, ["x"]])
def test_an_unanswered_or_empty_recall_is_quiet(remote, capsys, reply):
    db, install = remote
    install({"/hook/recall": reply})
    assert prompt_hook.run(_prompt()) == 0
    assert capsys.readouterr().out == ""
    assert not db.exists()


def test_a_local_brain_on_disk_is_never_the_fallback(remote, capsys, monkeypatch):
    db, install = remote
    db.parent.mkdir(parents=True)
    Database(db).connect()  # a real local brain sits right there
    install({"/hook/recall": None})
    monkeypatch.setattr("gingugu.embeddings.build_provider", _no_local_encoder)
    assert prompt_hook.run(_prompt()) == 0
    assert capsys.readouterr().out == ""


def test_a_corrupt_remote_setting_is_quiet_not_local(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memories.db"))
    monkeypatch.setenv("MEMORY_REMOTE_URL", "ftp://nope")
    monkeypatch.setattr("gingugu.embeddings.build_provider", _no_local_encoder)
    monkeypatch.setattr(sys, "stdin", _Stdin(json.dumps(_prompt())))
    assert prompt_hook.main() == 0
    assert capsys.readouterr().out == ""


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


# --- tripwires ---------------------------------------------------------------


def test_a_remote_tripwire_denies_and_logs_the_trip_upstream(remote, capsys):
    db, install = remote
    brain = install({"/hook/tripwires": {"tripwires": [WIRE]}, "/hook/trip": {"ok": True}})
    assert tool_hook.run(_tool()) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "Never --delete-branch a stack parent"
        in out["hookSpecificOutput"]["permissionDecisionReason"]
    )
    assert brain.paths() == ["/hook/tripwires", "/hook/trip"]
    assert brain.calls[0][1] == {"namespaces": ["crow", "gingugu"]}
    trip = brain.calls[1][1]
    assert trip["ids"] == ["m-merge"] and trip["session_id"] == "cc-1"
    assert "gh pr merge" in trip["text"]
    assert not db.exists()
    tool_hook.run(_tool())  # the re-issued call passes, from local state
    assert capsys.readouterr().out == ""


def test_rules_are_cached_for_a_minute(remote, monkeypatch):
    _, install = remote
    brain = install({"/hook/tripwires": {"tripwires": []}})
    clock = [1000.0]
    monkeypatch.setattr(tool_hook.time, "time", lambda: clock[0])
    tool_hook.run(_tool(command="ls"))
    clock[0] += 59
    tool_hook.run(_tool(command="ls"))
    assert brain.paths() == ["/hook/tripwires"]
    clock[0] += 2
    tool_hook.run(_tool(command="ls"))
    assert brain.paths() == ["/hook/tripwires", "/hook/tripwires"]


def test_the_cache_is_per_namespace_set(remote):
    _, install = remote
    brain = install({"/hook/tripwires": {"tripwires": []}})
    tool_hook.run(_tool(command="ls"))
    tool_hook.run({**_tool(command="ls"), "cwd": "/w/other-repo"})
    assert brain.paths() == ["/hook/tripwires", "/hook/tripwires"]


def test_the_last_rules_hold_while_the_brain_is_away(remote, capsys, monkeypatch):
    _, install = remote
    install({"/hook/tripwires": {"tripwires": [WIRE]}})
    clock = [1000.0]
    monkeypatch.setattr(tool_hook.time, "time", lambda: clock[0])
    tool_hook.run(_tool(command="ls"))  # fills the cache, trips nothing
    clock[0] += 3600
    install({"/hook/tripwires": None, "/hook/trip": None})
    tool_hook.run(_tool())
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_no_rules_and_no_brain_is_quiet(remote, capsys):
    _, install = remote
    install({"/hook/tripwires": None})
    assert tool_hook.run(_tool()) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("reply", [{"tripwires": [{"id": "only-an-id"}]}, {"tripwires": "x"}])
def test_malformed_rules_are_quiet(remote, capsys, reply):
    _, install = remote
    install({"/hook/tripwires": reply})
    assert tool_hook.run(_tool()) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_rules_cache_is_private(remote):
    db, install = remote
    install({"/hook/tripwires": {"tripwires": [WIRE]}})
    tool_hook.run(_tool(command="ls"))
    (cache,) = (db.parent / "hook-sessions").glob("tripwire-rules*.json")
    assert cache.stat().st_mode & 0o777 == 0o600


def test_our_own_tools_are_never_checked_remotely(remote):
    _, install = remote
    brain = install({})
    tool_hook.run({**_tool(), "tool_name": "mcp__gingugu__memory_store"})
    assert brain.calls == []
