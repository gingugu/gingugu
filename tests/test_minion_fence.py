"""The minion fence: what a subagent may not do to the brain behind its server.

Claude Code puts ``agent_id`` on a PreToolUse payload only when the call comes
from inside a subagent, so the main thread is never fenced. Inside one, three
doors are shut: the file tools on the brain's data directory, shell commands
that reach for the database or the CLI, and the write tools of the full-brain
``gingugu`` server every subagent inherits from its parent. The path checks
hold; the shell check is a speed bump, and the tests say which is which.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gingugu import tool_hook
from gingugu.minion_fence import decide

DATA = Path("/home/u/.local/share/gingugu")


def _p(tool: str, tool_input: dict, agent: str | None = "a1b2") -> dict:
    payload = {"session_id": "s", "cwd": "/repo", "tool_name": tool, "tool_input": tool_input}
    if agent is not None:
        payload["agent_id"] = agent
        payload["agent_type"] = "probe"
    return payload


@pytest.mark.parametrize(
    "tool,tool_input",
    [
        ("Read", {"file_path": str(DATA / "memories.db")}),
        ("Read", {"file_path": str(DATA / "serve_owner_token")}),
        ("Edit", {"file_path": str(DATA / "serve_tokens.json")}),
        ("Write", {"file_path": str(DATA / "x" / ".." / "memories.db")}),
        ("NotebookEdit", {"notebook_path": str(DATA / "n.ipynb")}),
        ("Grep", {"pattern": "token", "path": str(DATA)}),
        ("Glob", {"pattern": "*", "path": str(DATA)}),
        ("Glob", {"pattern": str(DATA / "*.json")}),
        # A search rooted ABOVE the data dir recurses into it.
        ("Grep", {"pattern": "token", "path": "/home/u"}),
        ("Grep", {"pattern": "token", "path": "/home/u/.local/share"}),
        ("Glob", {"pattern": "**/*.json", "path": "/"}),
        ("Glob", {"pattern": "/home/u/**/*.json"}),
    ],
)
def test_file_tools_are_shut_out_of_the_data_dir(tool, tool_input):
    assert decide(_p(tool, tool_input), DATA)


@pytest.mark.parametrize(
    "tool,tool_input",
    [
        ("Read", {"file_path": "/repo/src/gingugu/storage.py"}),
        ("Read", {"file_path": "/home/u/.local/share/gingugu-other/notes.md"}),
        ("Grep", {"pattern": "memories.db"}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Edit", {"file_path": "/repo/README.md"}),
        # Reading a single file above the data dir is not a search into it.
        ("Read", {"file_path": "/home/u/.zshrc"}),
        ("Grep", {"pattern": "x", "path": "/home/u/GIT/repo"}),
    ],
)
def test_file_tools_elsewhere_pass(tool, tool_input):
    assert decide(_p(tool, tool_input), DATA) is None


@pytest.mark.parametrize(
    "command",
    [
        "sqlite3 ~/.local/share/gingugu/memories.db 'select 1'",
        f"cat {DATA}/serve_owner_token",
        "python -c \"import sqlite3; sqlite3.connect('memories.db')\"",
        "gingugu token add me --ns '*=read'",
        "uv run gingugu dream",
        "cd /repo && .venv/bin/gingugu embed",
        "echo x | sqlite3 /tmp/copy.db",
        "MEMORY_DB_PATH=/x gingugu embed",
        "uvx gingugu token list",
        "uv run --project /repo gingugu dream",
        "python3 -m gingugu serve",
        "(gingugu ui)",
        "true; gingugu token revoke me",
    ],
)
def test_shell_reaching_for_the_brain_is_stopped(command):
    assert decide(_p("Bash", {"command": command}), DATA)


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -q tests/test_grants.py",
        "cd /Users/u/GIT/gingugu && git status",
        "ls src/gingugu",
        "grep -rn gingugu_hook src",
        'echo "Waiting for gingugu tools to connect..."',
        "git commit -m 'teach gingugu init the warm-up'",
        "grep -rn 'gingugu hook' docs",
        "cd gingugu && ls",
    ],
)
def test_ordinary_shell_in_the_gingugu_repo_passes(command):
    assert decide(_p("Bash", {"command": command}), DATA) is None


@pytest.mark.parametrize(
    "tool,tool_input",
    [
        ("mcp__gingugu__memory_store", {"title": "t"}),
        ("mcp__gingugu__memory_update", {"memory_id": "m"}),
        ("mcp__gingugu__memory_forget", {"memory_id": "m"}),
        ("mcp__gingugu__memory_relate", {}),
        ("mcp__gingugu__memory_unrelate", {}),
        ("mcp__gingugu__memory_consolidate", {}),
        ("mcp__gingugu__memory_import", {}),
        ("mcp__gingugu__memory_export", {}),
        ("mcp__gingugu__memory_dream", {}),
        ("mcp__gingugu__memory_tripwire", {"action": "add"}),
        ("mcp__gingugu__memory_tripwire", {"action": "remove"}),
        ("mcp__gingugu__memory_namespaces", {"action": "create"}),
        ("mcp__gingugu__credential_get", {"name": "x"}),
        ("mcp__gingugu__credential_store", {}),
        ("mcp__gingugu__credential_list", {}),
    ],
)
def test_inherited_full_brain_writes_are_stopped(tool, tool_input):
    assert decide(_p(tool, tool_input), DATA)


@pytest.mark.parametrize(
    "tool,tool_input",
    [
        ("mcp__gingugu__memory_recall", {"query": "q"}),
        ("mcp__gingugu__memory_search", {}),
        ("mcp__gingugu__memory_context", {}),
        ("mcp__gingugu__memory_stats", {}),
        ("mcp__gingugu__memory_excerpt", {}),
        ("mcp__gingugu__memory_edges", {}),
        ("mcp__gingugu__memory_namespaces", {}),
        ("mcp__gingugu__memory_namespaces", {"action": "list"}),
        ("mcp__gingugu__memory_tripwire", {"action": "list"}),
        ("mcp__gingugu__memory_tripwire", {"action": "test"}),
        # A minion's own fenced server is its server's business, not ours.
        ("mcp__brain__memory_store", {"namespace": "minions"}),
    ],
)
def test_reads_and_the_fenced_server_pass(tool, tool_input):
    assert decide(_p(tool, tool_input), DATA) is None


def test_a_search_from_a_cwd_above_the_data_dir_is_stopped():
    payload = _p("Grep", {"pattern": "token"})
    payload["cwd"] = "/home/u"
    assert decide(payload, DATA)


def test_the_main_thread_is_never_fenced():
    assert decide(_p("Bash", {"command": "sqlite3 memories.db"}, agent=None), DATA) is None
    assert decide(_p("mcp__gingugu__memory_store", {}, agent=None), DATA) is None


def test_malformed_input_never_raises():
    assert decide(_p("Read", None), DATA) is None  # type: ignore[arg-type]
    assert decide(_p("Bash", {"command": 7}), DATA) is None


# --- wired into ``gingugu hook tool`` ---------------------------------------


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    db = tmp_path / "data" / "memories.db"
    db.parent.mkdir()
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    monkeypatch.delenv("MEMORY_MINION_FENCE", raising=False)
    return db


def test_the_hook_denies_a_minion_even_with_tripwires_off(hook_env, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_TRIPWIRES", "off")
    payload = _p("Read", {"file_path": str(hook_env)})
    assert tool_hook.run(payload) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "minion" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_hook_denies_a_minions_inherited_write(hook_env, capsys):
    tool_hook.run(_p("mcp__gingugu__memory_store", {"title": "t"}))
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_fence_can_be_switched_off(hook_env, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_MINION_FENCE", "off")
    tool_hook.run(_p("Read", {"file_path": str(hook_env)}))
    assert capsys.readouterr().out == ""
