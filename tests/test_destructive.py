"""The big-delete gate: big or unrecoverable deletes reach the person, or fail.

An ordinary delete runs on the caller's ``confirm=true``. A namespace delete, a
credential delete, a consolidation or import over ``THRESHOLD`` memories, and
the hard deletes past ``THRESHOLD`` in one session get gingugu's PreToolUse hook
answering ``ask``: Claude Code's own permission prompt, which holds in auto and
bypass mode and turns into a deny where nobody can answer (measured
2026-11-05). The hook leaves a single-use approval for the call it asked about,
which the server - or the proxy, in remote mode - requires before it acts.
"""

from __future__ import annotations

import json

import pytest

from gingugu import destructive, tool_hook

N = destructive.THRESHOLD


def _ids(n: int) -> str:
    return ",".join(f"m{i}" for i in range(n))


def _data(n: int) -> dict:
    return {"memories": [{"id": f"m{i}"} for i in range(n)]}


# --- what is a big delete ------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool, args",
    [
        ("memory_namespaces", {"action": "delete", "name": "doomed", "cascade": True}),
        ("memory_namespaces", {"action": "delete", "name": "empty"}),
        ("credential_delete", {"service_name": "kraken-api"}),
        ("credential_delete", {"service_name": "kraken-api", "field_name": "token"}),
        ("memory_consolidate", {"memory_ids": _ids(N + 1), "keep_originals": False}),
        ("memory_import", {"data": _data(N + 1), "on_conflict": "replace"}),
    ],
)
def test_big_deletes_are_named(tool, args):
    reason = destructive.classify(tool, args)
    assert reason and "cannot be undone" in reason


@pytest.mark.parametrize(
    "tool, args",
    [
        ("memory_namespaces", {"action": "list"}),
        ("memory_namespaces", {"action": "create", "name": "x"}),
        ("memory_consolidate", {"memory_ids": _ids(N), "keep_originals": False}),
        ("memory_consolidate", {"memory_ids": _ids(N + 5)}),  # originals kept
        ("memory_import", {"data": _data(N), "on_conflict": "replace"}),
        ("memory_import", {"data": _data(N + 5)}),  # skip never overwrites
        ("memory_forget", {"memory_id": "m1", "hard_delete": True}),  # counted, not per call
        ("memory_store", {"title": "x"}),
    ],
)
def test_ordinary_calls_are_not(tool, args):
    assert destructive.classify(tool, args) is None


def test_the_reason_names_what_goes_and_cannot_be_reshaped():
    reason = destructive.classify(
        "memory_namespaces", {"action": "delete", "name": "a‮b\nc", "cascade": True}
    )
    assert "namespace" in reason and "‮" not in reason and "\n" not in reason


def test_mcp_tool_names_resolve_to_the_gingugu_tool():
    assert destructive.tool_of("mcp__gingugu__credential_delete") == "credential_delete"
    assert destructive.tool_of("mcp__brain__memory_forget") == "memory_forget"
    assert destructive.tool_of("Bash") == "Bash"


# --- the single-use approval ---------------------------------------------------------


def test_an_approval_is_taken_once(tmp_path):
    args = {"action": "delete", "name": "doomed", "cascade": True}
    assert destructive.take(tmp_path, "memory_namespaces", args) is False
    destructive.approve(tmp_path, "memory_namespaces", args)
    assert destructive.take(tmp_path, "memory_namespaces", args) is True
    assert destructive.take(tmp_path, "memory_namespaces", args) is False


def test_an_approval_is_for_that_target_only(tmp_path):
    destructive.approve(tmp_path, "memory_namespaces", {"action": "delete", "name": "a"})
    assert not destructive.take(tmp_path, "memory_namespaces", {"action": "delete", "name": "b"})
    assert not destructive.take(tmp_path, "credential_delete", {"service_name": "a"})


def test_server_defaults_do_not_change_the_target(tmp_path):
    # The hook sees what the model sent; the server sees it with defaults filled in.
    destructive.approve(tmp_path, "credential_delete", {"service_name": "svc", "confirm": True})
    full = {"service_name": "svc", "field_name": None, "confirm": True, "answer": None}
    assert destructive.take(tmp_path, "credential_delete", full) is True


def test_an_approval_expires(tmp_path):
    args = {"service_name": "svc"}
    destructive.approve(tmp_path, "credential_delete", args, now=1000.0)
    assert not destructive.take(
        tmp_path, "credential_delete", args, now=1000.0 + destructive.TTL + 1
    )


# --- the hook ---------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    db = tmp_path / "data" / "memories.db"
    db.parent.mkdir()
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    monkeypatch.setenv("MEMORY_TRIPWIRES", "off")
    return db.parent


def _call(tool: str, args: dict, session: str = "s1", **extra) -> dict:
    return {
        "session_id": session,
        "cwd": "/repo",
        "tool_name": f"mcp__gingugu__{tool}",
        "tool_input": args,
        "permission_mode": "bypassPermissions",
        **extra,
    }


def _decision(capsys) -> dict | None:
    out = capsys.readouterr().out.strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


def test_the_hook_asks_for_a_namespace_delete_even_in_bypass(data_dir, capsys):
    args = {"action": "delete", "name": "doomed", "cascade": True, "confirm": True}
    assert tool_hook.run(_call("memory_namespaces", args)) == 0
    decision = _decision(capsys)
    assert decision["permissionDecision"] == "ask"
    assert "doomed" in decision["permissionDecisionReason"]
    assert destructive.take(data_dir, "memory_namespaces", args) is True


def test_the_hook_stays_quiet_for_an_ordinary_delete(data_dir, capsys):
    tool_hook.run(_call("memory_forget", {"memory_id": "m1", "hard_delete": True}))
    assert _decision(capsys) is None


def test_the_hook_asks_once_a_session_passes_the_hard_delete_threshold(data_dir, capsys):
    for i in range(N):
        tool_hook.run(_call("memory_forget", {"memory_id": f"m{i}", "hard_delete": True}))
        assert _decision(capsys) is None
    tool_hook.run(_call("memory_forget", {"memory_id": "one-too-many", "hard_delete": True}))
    decision = _decision(capsys)
    assert decision["permissionDecision"] == "ask"
    assert str(N + 1) in decision["permissionDecisionReason"]
    # Soft deletes and other sessions do not count toward it.
    tool_hook.run(_call("memory_forget", {"memory_id": "soft"}, session="s2"))
    tool_hook.run(_call("memory_forget", {"memory_id": "x", "hard_delete": True}, session="s2"))
    assert _decision(capsys) is None


def test_the_hook_asks_inside_a_minion_too(data_dir, capsys):
    # A minion's own server is fenced, but the person still decides; a background
    # subagent that cannot prompt gets Claude Code's deny.
    payload = _call("credential_delete", {"service_name": "svc"}, agent_id="a1", agent_type="fork")
    tool_hook.run(payload)
    assert _decision(capsys)["permissionDecision"] == "ask"


def test_a_broken_data_dir_never_breaks_the_hook(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "missing" / "deeper" / "memories.db"))
    monkeypatch.setenv("MEMORY_TRIPWIRES", "off")
    assert tool_hook.run(_call("credential_delete", {"service_name": "svc"})) == 0
    # Still asks: failing to record the approval only costs a refusal later.
    assert _decision(capsys)["permissionDecision"] == "ask"
