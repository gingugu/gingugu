"""Remote mode: the warm-up hook asks the brain, and the transport under all
three hooks - owner token from the keychain, JSON in and out, None on any
failure so a hook stays quiet rather than breaking a turn."""

from __future__ import annotations

import json
import os

import pytest

from gingugu import hook_remote, subagent_hook
from tests.hook_remote_fixtures import URL, _no_local_encoder

# --- warm-up -----------------------------------------------------------------

SPEC = "gingugu=read,minions=write"


def _fenced_agent(root):
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "probe.md").write_text(
        "---\nname: probe\nmcpServers:\n  - brain:\n      command: gingugu\n      env:\n"
        f'        MEMORY_GRANT: "{SPEC}"\n---\nbody\n'
    )


def test_warmup_is_asked_of_the_brain(remote, tmp_path, monkeypatch, capsys):
    db, install = remote
    project = tmp_path / "gingugu"
    _fenced_agent(project)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setattr("gingugu.embeddings.build_provider", _no_local_encoder)
    brain = install({"/hook/warmup": {"context": "WARM-FROM-PI"}})
    subagent_hook.stash(db, {"session_id": "s1", "tool_input": _spawn()})
    assert subagent_hook.run({"agent_type": "probe", "session_id": "s1"}) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["additionalContext"] == "WARM-FROM-PI"
    ((path, body),) = brain.calls
    assert path == "/hook/warmup"
    assert body == {
        "spec": SPEC,
        "task_hint": "audit the rollout docs",
        "agent_type": "probe",
        "namespaces": ["crow", "gingugu"],
    }
    assert not db.exists()


def _spawn():
    return {"prompt": "audit the rollout docs", "subagent_type": "probe"}


def test_an_unfenced_agent_never_reaches_the_brain(remote, tmp_path, monkeypatch, capsys):
    _, install = remote
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "empty"))
    brain = install({})
    subagent_hook.run({"agent_type": "probe", "session_id": "s1"})
    assert brain.calls == [] and capsys.readouterr().out == ""


@pytest.mark.parametrize("reply", [None, {"context": None}, {"context": 7}])
def test_an_unanswered_warmup_is_quiet(remote, tmp_path, monkeypatch, capsys, reply):
    _, install = remote
    project = tmp_path / "gingugu"
    _fenced_agent(project)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    install({"/hook/warmup": reply})
    assert subagent_hook.run({"agent_type": "probe", "session_id": "s1"}) == 0
    assert capsys.readouterr().out == ""


# --- the transport -----------------------------------------------------------


def test_post_sends_the_owner_token_and_returns_the_json(monkeypatch):
    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(hook_remote, "_transport", lambda: httpx.MockTransport(handler))
    monkeypatch.setattr("gingugu.remote._kr_get", lambda url: "owner-" + "t" * 40)
    target = hook_remote.RemoteTarget(URL, "env")
    assert hook_remote.post(target, "/hook/recall", {"a": 1}, timeout=1.0) == {"ok": True}
    assert seen == {
        "auth": "Bearer owner-" + "t" * 40,
        "url": URL + "/hook/recall",
        "body": {"a": 1},
    }


@pytest.mark.parametrize(
    "status,token", [(403, "owner-" + "t" * 40), (500, "owner-" + "t" * 40), (200, None)]
)
def test_post_is_none_on_any_failure(monkeypatch, status, token):
    import httpx

    monkeypatch.setattr(
        hook_remote,
        "_transport",
        lambda: httpx.MockTransport(lambda r: httpx.Response(status, json={"ok": True})),
    )
    monkeypatch.setattr("gingugu.remote._kr_get", lambda url: token)
    target = hook_remote.RemoteTarget(URL, "env")
    assert hook_remote.post(target, "/hook/recall", {}, timeout=1.0) is None


def test_post_survives_a_dead_network(monkeypatch):
    import httpx

    def boom(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(hook_remote, "_transport", lambda: httpx.MockTransport(boom))
    monkeypatch.setattr("gingugu.remote._kr_get", lambda url: "owner-" + "t" * 40)
    target = hook_remote.RemoteTarget(URL, "env")
    assert hook_remote.post(target, "/hook/recall", {}, timeout=1.0) is None


def test_target_is_none_in_local_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("MEMORY_REMOTE_URL", raising=False)
    monkeypatch.setattr("gingugu.remote.settings_path", lambda: tmp_path / "none.json")
    assert hook_remote.target() is None
    assert os.environ.get("MEMORY_REMOTE_URL") is None
