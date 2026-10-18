"""Hook recall honours the client's grant (``MEMORY_GRANT``).

A client fenced to ``tyrone=write,crow=read`` has its MCP calls fenced by the
proxy's derived token, but the hooks pick their namespaces from crow, the
persona and the cwd basename - and in remote mode send them up on the owner
token. Without the grant applied here a fenced client is shown any namespace
its working folder names, and ChatGPT's per-chat folder adds a junk one.

So the hooks apply the same grant: a name the grant cannot read is dropped
before any lookup, and a grant that cannot be honoured (empty, malformed,
``*=write``) fails closed - no namespaces, a quiet hook - never the whole brain.
"""

from __future__ import annotations

import pytest

from gingugu import prompt_hook, tool_hook
from gingugu.prompt_hook import namespaces_for
from tests.hook_remote_fixtures import WIRE, _prompt, _tool

CHAT = "/Users/me/Documents/Codex/2026-10-04/plan-the-week"
TYRONE = "tyrone=write,crow=read"


@pytest.fixture(autouse=True)
def no_fence(monkeypatch):
    monkeypatch.delenv("MEMORY_GRANT", raising=False)
    monkeypatch.delenv("MEMORY_PERSONA", raising=False)


# --- namespaces_for ----------------------------------------------------------


def test_no_grant_is_unchanged():
    assert namespaces_for("/home/me/gingugu") == ["crow", "gingugu"]


def test_a_fenced_client_keeps_only_what_it_can_read(monkeypatch):
    monkeypatch.setenv("MEMORY_PERSONA", "tyrone")
    monkeypatch.setenv("MEMORY_GRANT", TYRONE)
    assert namespaces_for(CHAT) == ["crow", "tyrone"]


def test_a_repo_the_grant_does_not_name_is_dropped(monkeypatch):
    monkeypatch.setenv("MEMORY_GRANT", TYRONE)
    assert namespaces_for("/home/me/devex-secret-repo") == ["crow"]


def test_crow_is_dropped_when_the_grant_omits_it(monkeypatch):
    monkeypatch.setenv("MEMORY_GRANT", "gingugu=read")
    assert namespaces_for("/home/me/gingugu") == ["gingugu"]


def test_a_wildcard_read_keeps_the_derived_names(monkeypatch):
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    monkeypatch.setenv("MEMORY_GRANT", "*=read,beepboop=write")
    assert namespaces_for("/home/me/gingugu") == ["crow", "beepboop", "gingugu"]


def test_the_grant_never_adds_a_namespace(monkeypatch):
    monkeypatch.setenv("MEMORY_GRANT", "crow=read,gingugu=read,elsewhere=write")
    assert namespaces_for("/home/me/gingugu") == ["crow", "gingugu"]


@pytest.mark.parametrize(
    "bad", ["", "   ", "nonsense", "crow=admin", "*=write", "crow=read,crow=write"]
)
def test_a_grant_that_cannot_be_honoured_fails_closed(monkeypatch, bad):
    monkeypatch.setenv("MEMORY_GRANT", bad)
    assert namespaces_for("/home/me/gingugu") == []


# --- the hooks, remote mode --------------------------------------------------


def test_recall_sends_only_granted_namespaces(remote, monkeypatch):
    _, install = remote
    monkeypatch.setenv("MEMORY_PERSONA", "tyrone")
    monkeypatch.setenv("MEMORY_GRANT", TYRONE)
    brain = install({"/hook/recall": {"context": None, "ids": []}})
    prompt_hook.run({**_prompt(), "cwd": CHAT})
    assert brain.calls[0][1]["namespaces"] == ["crow", "tyrone"]


@pytest.mark.parametrize("grant", ["elsewhere=read", "nonsense"])
def test_recall_with_nothing_readable_never_reaches_the_network(remote, capsys, monkeypatch, grant):
    _, install = remote
    monkeypatch.setenv("MEMORY_GRANT", grant)
    brain = install({"/hook/recall": {"context": "LEAK", "ids": ["m1"]}})
    assert prompt_hook.run(_prompt()) == 0
    assert brain.calls == []
    assert capsys.readouterr().out == ""


def test_tripwires_ask_only_for_granted_namespaces(remote, monkeypatch):
    _, install = remote
    monkeypatch.setenv("MEMORY_GRANT", "crow=read")
    brain = install({"/hook/tripwires": {"tripwires": [WIRE]}, "/hook/trip": {"ok": True}})
    tool_hook.run(_tool())
    assert brain.calls[0] == ("/hook/tripwires", {"namespaces": ["crow"]})


@pytest.mark.parametrize("grant", ["elsewhere=read", "*=write"])
def test_tripwires_with_nothing_readable_never_reach_the_network(
    remote, capsys, monkeypatch, grant
):
    _, install = remote
    monkeypatch.setenv("MEMORY_GRANT", grant)
    brain = install({"/hook/tripwires": {"tripwires": [WIRE]}})
    assert tool_hook.run(_tool()) == 0
    assert brain.calls == []
    assert capsys.readouterr().out == ""
