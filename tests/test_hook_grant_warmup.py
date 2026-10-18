"""Minion warm-up honours the client's grant too.

The warm-up loads what the minion's agent file grants. A fenced client that
spawns a minion must not be warmed from a namespace its own grant cannot read,
so the agent file's spec is narrowed to the client's ``MEMORY_GRANT`` before it
is used - locally or sent to the brain. A ``*`` entry stays: its reach is the
client's own (already narrowed) namespace list.
"""

from __future__ import annotations

import pytest

from gingugu import subagent_hook
from gingugu.hook_grant import narrow_spec
from tests.hook_remote_fixtures import _no_local_encoder

TYRONE = "tyrone=write,crow=read"


@pytest.fixture(autouse=True)
def no_fence(monkeypatch):
    monkeypatch.delenv("MEMORY_GRANT", raising=False)
    monkeypatch.delenv("MEMORY_PERSONA", raising=False)


def test_no_client_grant_leaves_the_spec_alone():
    assert narrow_spec("gingugu=read,minions=write") == "gingugu=read,minions=write"


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("crow=read,minions=write", "crow=read"),
        ("crow=read,tyrone=read,gingugu=read", "crow=read,tyrone=read"),
        ("*=read,minions=write", "*=read"),
        ("gingugu=read,minions=write", None),
    ],
)
def test_the_spec_keeps_only_what_the_client_can_read(monkeypatch, spec, expected):
    monkeypatch.setenv("MEMORY_GRANT", TYRONE)
    assert narrow_spec(spec) == expected


@pytest.mark.parametrize("bad", ["", "nonsense", "*=write"])
def test_a_client_grant_that_cannot_be_honoured_warms_nothing(monkeypatch, bad):
    monkeypatch.setenv("MEMORY_GRANT", bad)
    assert narrow_spec("crow=read") is None


def test_an_unparseable_minion_spec_warms_nothing(monkeypatch):
    monkeypatch.setenv("MEMORY_GRANT", TYRONE)
    assert narrow_spec("crow=admin") is None


def _agent(project, spec):
    agents = project / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "probe.md").write_text(
        "---\nname: probe\nmcpServers:\n  - brain:\n      command: gingugu\n      env:\n"
        f'        MEMORY_GRANT: "{spec}"\n---\nbody\n'
    )


def test_remote_warmup_sends_the_narrowed_spec(remote, tmp_path, monkeypatch):
    _, install = remote
    project = tmp_path / "gingugu"
    _agent(project, "crow=read,gingugu=read,minions=write")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("MEMORY_GRANT", TYRONE)
    monkeypatch.setattr("gingugu.embeddings.build_provider", _no_local_encoder)
    brain = install({"/hook/warmup": {"context": None}})
    subagent_hook.run({"agent_type": "probe", "session_id": "s1"})
    ((path, body),) = brain.calls
    assert body["spec"] == "crow=read"
    assert body["namespaces"] == ["crow"]


def test_a_minion_wholly_outside_the_client_grant_never_reaches_the_network(
    remote, tmp_path, monkeypatch, capsys
):
    _, install = remote
    project = tmp_path / "gingugu"
    _agent(project, "gingugu=read,minions=write")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("MEMORY_GRANT", TYRONE)
    brain = install({"/hook/warmup": {"context": "LEAK"}})
    assert subagent_hook.run({"agent_type": "probe", "session_id": "s1"}) == 0
    assert brain.calls == []
    assert capsys.readouterr().out == ""
