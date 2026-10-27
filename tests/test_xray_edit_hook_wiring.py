"""X2 wired into ``gingugu hook tool``: one deny, its own switch, never a crash.

The X-ray itself is specified in ``test_xray_edit_hook``; this is how the
PreToolUse entry point combines it with memory tripwires.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gingugu import tool_hook
from gingugu.xray import edit_hook
from tests.test_xray_edit_hook import BASIC, _payload, _repo


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "db" / "memories.db"))
    monkeypatch.delenv("MEMORY_TRIPWIRES", raising=False)
    monkeypatch.delenv("MEMORY_XRAY_TRIPWIRE", raising=False)
    return tmp_path


def test_the_hook_denies_with_the_xray_as_the_reason(repo, hook_env, capsys):
    assert tool_hook.run(_payload(repo, "pkg/core.py")) == 0
    out = json.loads(capsys.readouterr().out)
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert "pkg/a.py" in spec["permissionDecisionReason"]
    assert "x-ray" in out["systemMessage"].lower()


def test_the_reissued_edit_passes(repo, hook_env, capsys):
    tool_hook.run(_payload(repo, "pkg/core.py"))
    capsys.readouterr()
    tool_hook.run(_payload(repo, "pkg/core.py"))
    assert capsys.readouterr().out == ""


def test_its_own_switch_turns_it_off(repo, hook_env, capsys, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_TRIPWIRE", "off")
    tool_hook.run(_payload(repo, "pkg/core.py"))
    assert capsys.readouterr().out == ""


def test_memory_tripwires_off_does_not_turn_it_off(repo, hook_env, capsys, monkeypatch):
    monkeypatch.setenv("MEMORY_TRIPWIRES", "off")
    tool_hook.run(_payload(repo, "pkg/core.py"))
    assert "pkg/a.py" in capsys.readouterr().out


def test_a_crashing_xray_never_breaks_the_hook(repo, hook_env, capsys, monkeypatch):
    def boom(payload, state_dir):
        raise RuntimeError("bug")

    monkeypatch.setattr(edit_hook, "check", boom)
    assert tool_hook.run(_payload(repo, "pkg/core.py")) == 0
    assert capsys.readouterr().out == ""


def test_a_memory_tripwire_and_the_xray_share_one_deny(repo, hook_env, capsys):
    from gingugu import tripwire
    from gingugu.config import load_config
    from gingugu.database import Database
    from gingugu.models import MemoryType
    from gingugu.namespaces import NamespaceManager
    from gingugu.prompt_hook import namespaces_for
    from gingugu.storage import MemoryStore

    db_path = load_config().db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(db_path)
    conn = database.connect()
    ns = NamespaceManager(conn, load_config()).get_or_create(namespaces_for(str(repo))[0])
    mem = MemoryStore(conn).create(
        namespace_id=ns.id,
        type=MemoryType.PREFERENCE,
        title="core.py is load-bearing",
        content="Run the whole suite after touching it.",
    )
    tripwire.add_tripwire(conn, mem.id, "Edit", r"core\.py")
    conn.commit()
    database.close()

    tool_hook.run(_payload(repo, "pkg/core.py"))
    out = capsys.readouterr().out
    assert out.count("hookSpecificOutput") == 1  # one JSON object, not two
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "core.py is load-bearing" in reason
    assert "pkg/a.py" in reason


def test_the_state_dir_is_beside_the_db(repo, hook_env, capsys):
    tool_hook.run(_payload(repo, "pkg/core.py"))
    state_dir = Path(os.environ["MEMORY_DB_PATH"]).parent / "hook-sessions"
    assert list(state_dir.glob("xray-graph-*.json"))


def test_a_crashing_memory_tripwire_still_lets_the_xray_speak(repo, hook_env, capsys, monkeypatch):
    def boom(payload):
        raise RuntimeError("bug")

    monkeypatch.setattr(tool_hook, "_memory_trip", boom)
    assert tool_hook.run(_payload(repo, "pkg/core.py")) == 0
    assert "pkg/a.py" in capsys.readouterr().out
