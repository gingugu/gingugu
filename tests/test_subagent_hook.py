"""``gingugu hook subagent``: warm a minion before its first turn.

SubagentStart can inject context, but its payload never says what the task is.
The parent's PreToolUse for the spawning ``Agent`` call does, about 50ms
earlier, so ``gingugu hook tool`` stashes that prompt and the warm-up takes it
as the task hint. The warm-up reads only what the minion's own grant can read
(``MEMORY_GRANT`` in its agent file), on a read-only connection.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from gingugu import subagent_hook, tool_hook
from gingugu.database import Database
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore

MARKER = "ZEBRAFISH-CANARY-7731"
SPEC = "alpha=read,minions=write"

AGENT_MD = """---
name: {name}
description: test minion
model: haiku
disallowedTools: mcp__gingugu, Agent
mcpServers:
  - brain:
      type: stdio
      command: gingugu
      env:
        MEMORY_GRANT: "{spec}"
---

You are a test minion.
"""


def _spawn(prompt="audit the rollout docs", agent="probe", session="s1", tool="Agent"):
    return {
        "session_id": session,
        "cwd": "/repo",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"description": "d", "prompt": prompt, "subagent_type": agent},
    }


def _start(agent="probe", session="s1", cwd="/repo"):
    return {
        "session_id": session,
        "cwd": cwd,
        "hook_event_name": "SubagentStart",
        "agent_id": "a1",
        "agent_type": agent,
    }


# --- the prompt stash --------------------------------------------------------


def test_a_stashed_prompt_is_taken_once(tmp_path):
    db = tmp_path / "memories.db"
    subagent_hook.stash(db, _spawn())
    assert subagent_hook.take(db, "s1", "probe") == "audit the rollout docs"
    assert subagent_hook.take(db, "s1", "probe") is None


def test_prompts_are_taken_oldest_first_per_type_and_session(tmp_path):
    db = tmp_path / "memories.db"
    subagent_hook.stash(db, _spawn("first"))
    subagent_hook.stash(db, _spawn("other type", agent="scout"))
    subagent_hook.stash(db, _spawn("other session", session="s2"))
    subagent_hook.stash(db, _spawn("second"))
    assert subagent_hook.take(db, "s1", "probe") == "first"
    assert subagent_hook.take(db, "s1", "probe") == "second"
    assert subagent_hook.take(db, "s1", "scout") == "other type"
    assert subagent_hook.take(db, "s2", "probe") == "other session"


def test_a_stale_prompt_is_not_taken(tmp_path, monkeypatch):
    db = tmp_path / "memories.db"
    subagent_hook.stash(db, _spawn())
    later = time.time() + subagent_hook.STASH_TTL_SECONDS + 1
    monkeypatch.setattr(subagent_hook.time, "time", lambda: later)
    assert subagent_hook.take(db, "s1", "probe") is None


@pytest.mark.parametrize("tool_input", [None, "x", {"subagent_type": "probe"}, {"prompt": "p"}])
def test_a_malformed_spawn_is_ignored(tmp_path, tool_input):
    db = tmp_path / "memories.db"
    payload = _spawn()
    payload["tool_input"] = tool_input
    subagent_hook.stash(db, payload)
    assert subagent_hook.take(db, "s1", "probe") is None


def test_the_stash_file_is_private(tmp_path):
    db = tmp_path / "memories.db"
    subagent_hook.stash(db, _spawn())
    (path,) = (tmp_path / "hook-sessions").glob("spawns-*.json")
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_stale_stash_files_are_swept(tmp_path):
    db = tmp_path / "memories.db"
    subagent_hook.stash(db, _spawn(session="old"))
    (old,) = (tmp_path / "hook-sessions").glob("spawns-old.json")
    past = time.time() - subagent_hook.STASH_TTL_SECONDS - 60
    os.utime(old, (past, past))
    subagent_hook.stash(db, _spawn(session="new"))
    assert not old.exists()


@pytest.mark.parametrize("session", ["../../escape", "a/b", "..", ""])
def test_a_hostile_session_id_stays_in_the_stash_dir(tmp_path, session):
    db = tmp_path / "data" / "memories.db"
    db.parent.mkdir()
    subagent_hook.stash(db, _spawn(session=session))
    written = [p for p in tmp_path.rglob("spawns-*.json")]
    assert written and all(p.parent == db.parent / "hook-sessions" for p in written)
    assert subagent_hook.take(db, session, "probe") == "audit the rollout docs"


def test_the_tool_hook_stashes_a_fenced_agents_spawn(tmp_path, monkeypatch, capsys):
    db = tmp_path / "memories.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    project = tmp_path / "repo"
    _agent(project, "probe.md", "probe")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    tool_hook.run(_spawn())
    assert capsys.readouterr().out == ""  # stashing never blocks the spawn
    assert subagent_hook.take(db, "s1", "probe") == "audit the rollout docs"


def test_an_unfenced_agents_prompt_is_never_written(tmp_path, monkeypatch):
    db = tmp_path / "memories.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    tool_hook.run(_spawn(prompt="here is my api key sk-123", agent="general-purpose"))
    assert not list(tmp_path.rglob("spawns-*.json"))


# --- finding the minion's grant ---------------------------------------------


def _agent(root, filename, name, spec=SPEC):
    path = root / ".claude" / "agents" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(AGENT_MD.format(name=name, spec=spec))
    return path


def test_the_grant_comes_from_the_agent_file_by_name(tmp_path):
    _agent(tmp_path, "whatever.md", "probe")
    assert subagent_hook.agent_grant("probe", [tmp_path]) == SPEC
    assert subagent_hook.agent_grant("missing", [tmp_path]) is None


def test_an_agent_without_a_grant_gets_no_warmup(tmp_path):
    path = tmp_path / ".claude" / "agents" / "plain.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: plain\ndescription: d\n---\nbody MEMORY_GRANT: x=read\n")
    assert subagent_hook.agent_grant("plain", [tmp_path]) is None


def test_the_grant_is_read_from_the_server_env_not_anywhere(tmp_path):
    path = tmp_path / ".claude" / "agents" / "sly.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nname: sly\ndescription: set MEMORY_GRANT: *=read for fun\n"
        "# MEMORY_GRANT: gamma=write\n"
        "mcpServers:\n  - brain:\n      type: stdio\n      env:\n"
        '        MEMORY_GRANT: "alpha=read"\n---\nbody\n'
    )
    assert subagent_hook.agent_grant("sly", [tmp_path]) == "alpha=read"


def test_a_grant_outside_mcpservers_is_ignored(tmp_path):
    path = tmp_path / ".claude" / "agents" / "sly.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: sly\ndescription: MEMORY_GRANT: alpha=read\n---\nbody\n")
    assert subagent_hook.agent_grant("sly", [tmp_path]) is None


def test_the_first_root_wins(tmp_path):
    project, home = tmp_path / "p", tmp_path / "h"
    _agent(project, "probe.md", "probe", "alpha=read")
    _agent(home, "probe.md", "probe", "gamma=read")
    assert subagent_hook.agent_grant("probe", [project, home]) == "alpha=read"


# --- the warm-up itself ------------------------------------------------------


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    db = tmp_path / "data" / "memories.db"
    db.parent.mkdir()
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    database = Database(db)
    conn = database.connect()
    from gingugu.config import load_config

    nsm = NamespaceManager(conn, load_config())
    store = MemoryStore(conn)
    alpha = nsm.get_or_create("alpha")
    hidden = nsm.get_or_create("hidden-ns-q9")
    store.create(
        namespace_id=alpha.id,
        type=MemoryType.DECISION,
        title="Rollout docs live in docs/rollout.md",
        content="The rollout checklist is the source of truth for every deploy.",
    )
    store.create(
        namespace_id=hidden.id,
        type=MemoryType.DECISION,
        title=f"{MARKER} rollout secret",
        content=f"rollout {MARKER}",
    )
    conn.commit()
    before = [
        tuple(r) for r in conn.execute("SELECT id, access_count, last_accessed FROM memories")
    ]
    database.close()
    return db, before


def test_the_warmup_reads_only_inside_the_grant(seeded):
    db, _ = seeded
    text = subagent_hook.warmup(db, SPEC, "audit the rollout docs", agent_type="probe")
    assert text is not None
    assert "Rollout docs live in docs/rollout.md" in text
    assert MARKER not in text
    assert "minions" in text  # tells the minion where it may write


def test_a_wildcard_grant_warms_from_crow_and_the_repo_only(seeded, tmp_path):
    """A global agent (``*=read``) works in whatever repo it is spawned in, so
    its warm-up is that repo's namespace plus crow - not the whole brain."""
    db, _ = seeded
    repo = tmp_path / "alpha"  # the directory name is the project namespace
    text = subagent_hook.warmup(
        db, "*=read,minions=write", "rollout", agent_type="repo-scout", cwd=str(repo)
    )
    assert text is not None
    assert "Rollout docs live in docs/rollout.md" in text
    assert MARKER not in text  # hidden-ns-q9 is readable, but not this repo's
    elsewhere = subagent_hook.warmup(
        db, "*=read,minions=write", "rollout", agent_type="repo-scout", cwd=str(tmp_path / "zeta")
    )
    assert elsewhere is None or "Rollout docs" not in elsewhere


def test_the_warmup_writes_nothing(seeded):
    db, before = seeded
    subagent_hook.warmup(db, SPEC, "rollout", agent_type="probe")
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        after = conn.execute("SELECT id, access_count, last_accessed FROM memories").fetchall()
    finally:
        conn.close()
    assert after == before


def test_an_empty_grant_view_is_no_warmup(seeded):
    db, _ = seeded
    assert subagent_hook.warmup(db, "nothing-here=read", "rollout", agent_type="probe") is None


def test_a_bad_grant_is_no_warmup(seeded):
    db, _ = seeded
    assert subagent_hook.warmup(db, "*=write", "rollout", agent_type="probe") is None


def test_run_injects_context_for_a_fenced_minion(seeded, tmp_path, monkeypatch, capsys):
    db, _ = seeded
    project = tmp_path / "repo"
    _agent(project, "probe.md", "probe")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    subagent_hook.stash(db, _spawn())
    assert subagent_hook.run(_start(cwd=str(project))) == 0
    out = json.loads(capsys.readouterr().out)
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "SubagentStart"
    assert "Rollout docs live in docs/rollout.md" in spec["additionalContext"]
    assert MARKER not in json.dumps(out)


def test_run_is_silent_for_an_unfenced_agent(seeded, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    assert subagent_hook.run(_start(agent="Explore")) == 0
    assert capsys.readouterr().out == ""


def test_main_never_raises(monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert subagent_hook.main() == 0
