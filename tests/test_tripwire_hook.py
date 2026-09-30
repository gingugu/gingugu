"""``gingugu hook tool``: the PreToolUse side of tripwires.

The hook denies the first matching call in a session with the memory as the
reason, and lets the re-issued call through. Deny is the only lever: a
PreToolUse ``additionalContext`` is delivered next to the tool RESULT, which is
after the call has already run.
"""

from __future__ import annotations

import io
import json
import sqlite3

import pytest

from gingugu import prompt_hook, tool_hook, tripwire
from gingugu.database import Database
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore

MERGE = {"command": "gh pr merge 90 --squash --delete-branch"}


@pytest.fixture
def brain(tmp_path, monkeypatch):
    path = tmp_path / "memories.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(path))
    monkeypatch.delenv("MEMORY_TRIPWIRES", raising=False)
    database = Database(path)
    conn = database.connect()
    from gingugu.config import load_config

    ns = NamespaceManager(conn, load_config()).get_or_create("crow")
    mem = MemoryStore(conn).create(
        namespace_id=ns.id,
        type=MemoryType.PREFERENCE,
        title="Never --delete-branch a stack parent",
        content="Retarget the children to main first; GitHub will not reopen them.",
    )
    tripwire.add_tripwire(conn, mem.id, "Bash", r"gh pr merge.*--delete-branch")
    conn.commit()
    database.close()
    return path, mem.id


def _payload(tool_input=MERGE, tool="Bash", session="cc-1", cwd="/tmp/gingugu"):
    return {
        "session_id": session,
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_use_id": "toolu_1",
    }


def _log(path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT tool, query, result_ids, session_id FROM query_log").fetchall()
    finally:
        conn.close()


def test_a_matching_call_is_denied_with_the_memory_as_the_reason(brain, capsys):
    path, mem_id = brain
    assert tool_hook.run(_payload()) == 0
    out = json.loads(capsys.readouterr().out)
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "Never --delete-branch a stack parent" in spec["permissionDecisionReason"]
    assert mem_id in spec["permissionDecisionReason"]
    assert out["systemMessage"].startswith("gingugu: tripwire")  # top-level, user-visible


def test_the_reissued_call_passes(brain, capsys):
    tool_hook.run(_payload())
    capsys.readouterr()
    assert tool_hook.run(_payload()) == 0
    assert capsys.readouterr().out == ""


def test_a_new_session_trips_again(brain, capsys):
    tool_hook.run(_payload(session="a"))
    capsys.readouterr()
    tool_hook.run(_payload(session="b"))
    assert "deny" in capsys.readouterr().out


def test_a_trip_is_logged_and_a_miss_is_not(brain, capsys):
    path, mem_id = brain
    tool_hook.run(_payload(tool_input={"command": "ls -la"}))
    assert _log(path) == []
    tool_hook.run(_payload())
    ((tool, query, result_ids, session),) = _log(path)
    assert tool == "tripwire"
    assert query == MERGE["command"]
    assert json.loads(result_ids) == [mem_id]
    assert session == "cc-1"


def test_a_non_matching_call_is_silent(brain, capsys):
    assert tool_hook.run(_payload(tool_input={"command": "gh pr merge 90 --squash"})) == 0
    assert capsys.readouterr().out == ""


def test_gingugus_own_tools_never_trip(brain, capsys):
    tool_hook.run(_payload(tool="mcp__gingugu__memory_store", tool_input=MERGE))
    assert capsys.readouterr().out == ""


def test_a_crow_tripwire_fires_in_any_repo(brain, capsys):
    tool_hook.run(_payload(cwd="/tmp/some-other-repo"))
    assert "deny" in capsys.readouterr().out


def test_disabled_by_env(brain, capsys, monkeypatch):
    monkeypatch.setenv("MEMORY_TRIPWIRES", "off")
    tool_hook.run(_payload())
    assert capsys.readouterr().out == ""


def test_a_missing_db_is_silent_and_never_created(tmp_path, monkeypatch, capsys):
    path = tmp_path / "nope.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(path))
    assert tool_hook.run(_payload()) == 0
    assert capsys.readouterr().out == ""
    assert not path.exists()


def test_an_unmigrated_db_is_silent(tmp_path, monkeypatch, capsys):
    path = tmp_path / "old.db"
    sqlite3.connect(path).close()
    monkeypatch.setenv("MEMORY_DB_PATH", str(path))
    assert tool_hook.run(_payload()) == 0
    assert capsys.readouterr().out == ""


def test_trip_state_does_not_touch_the_prompt_hooks_state(brain, capsys):
    path, _ = brain
    prompt_hook.save_suppressed(path, "cc-1", {"prompt-mem"})
    tool_hook.run(_payload())
    assert prompt_hook.load_suppressed(path, "cc-1") == {"prompt-mem"}


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]"])
def test_main_never_raises_on_garbage(stdin, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    assert tool_hook.main() == 0
    assert capsys.readouterr().out == ""
