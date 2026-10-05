"""The hooks `gingugu harness` installs, run the way Claude Code runs them.

Each is executed as a subprocess on the event payload, with the interpreter
running the suite - so a hook that needs a third-party package fails here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from gingugu.bootstrap.harness import main


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "my-repo"
    path.mkdir()
    assert main(["--path", str(path)]) == 0
    return path


def _hook(repo, name, payload, *args, cwd=None):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(repo / ".claude" / "hooks" / name), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or repo,
        timeout=30,
    )


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# --- log_event.py ----------------------------------------------------------------


def test_log_event_appends_one_json_line_per_event(repo):
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Read"}
    assert _hook(repo, "log_event.py", payload).returncode == 0
    assert _hook(repo, "log_event.py", payload).returncode == 0

    entries = _lines(repo / "logs" / "post_tool_use.jsonl")
    assert len(entries) == 2
    assert entries[0]["tool_name"] == "Read"
    assert "logged_at" in entries[0]


def test_log_event_writes_under_the_project_not_the_cwd(repo, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _hook(repo, "log_event.py", {"hook_event_name": "CwdChanged"}, cwd=elsewhere)
    assert (repo / "logs" / "cwd_changed.jsonl").exists()
    assert not (elsewhere / "logs").exists()


def test_log_event_never_fails_the_session(repo):
    result = _hook(repo, "log_event.py", "not json at all")
    assert result.returncode == 0
    assert result.stdout == ""


# --- pre_tool_use.py (the guard) ---------------------------------------------------


@pytest.mark.parametrize(
    "tool, tool_input",
    [
        ("Read", {"file_path": "/work/app/.env"}),
        ("Write", {"file_path": "/work/app/secrets.json", "content": "x"}),
        ("Read", {"file_path": "/home/me/.ssh/id_ed25519.pem"}),
        ("Bash", {"command": "cat .env"}),
        ("Bash", {"command": "rm -rf /"}),
        # An ls/find/git prefix exempts only a lone command, not a chain.
        ("Bash", {"command": "ls; cat .env"}),
        ("Bash", {"command": "git status && cat ~/.ssh/id_rsa"}),
        # .pem/.key mid-command, not only at the very end.
        ("Bash", {"command": "cat server.key | base64"}),
        # Grep returns file contents, so it is a read.
        ("Grep", {"pattern": "KEY", "path": "/work/app/.env"}),
        ("Grep", {"pattern": "KEY", "glob": ".env*"}),
    ],
)
def test_guard_blocks(repo, tool, tool_input):
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    result = _hook(repo, "pre_tool_use.py", payload)
    assert result.returncode == 2
    assert "BLOCKED" in result.stderr


@pytest.mark.parametrize(
    "tool, tool_input",
    [
        ("Read", {"file_path": "/work/app/README.md"}),
        ("Write", {"file_path": "/work/app/.env.example", "content": "KEY="}),
        ("Bash", {"command": "ls -la"}),
        ("Bash", {"command": "rm build/output.txt"}),
        ("Bash", {"command": "git log --oneline"}),
        ("Read", {"file_path": "/work/app/docs/keychain.md"}),
        ("Grep", {"pattern": "def main", "path": "/work/app/src"}),
    ],
)
def test_guard_allows(repo, tool, tool_input):
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    assert _hook(repo, "pre_tool_use.py", payload).returncode == 0


SECRET = "SUPERSECRET123"


def test_guard_never_logs_what_it_blocked(repo):
    write = {"file_path": "/work/app/.env", "content": f"TOKEN={SECRET}"}
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": write}
    assert _hook(repo, "pre_tool_use.py", payload).returncode == 2
    bash = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    _hook(repo, "pre_tool_use.py", {**bash, "tool_input": {"command": f"export T={SECRET}"}})

    log = repo / "logs" / "pre_tool_use.jsonl"
    assert SECRET not in (log.read_text() if log.exists() else "")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/work/app/a.txt", "content": SECRET},
            "tool_response": {"stdout": SECRET},
        },
        {"hook_event_name": "UserPromptSubmit", "prompt": f"my key is {SECRET}"},
        {"hook_event_name": "ElicitationResult", "content": {"password": SECRET}},
    ],
)
def test_log_event_records_metadata_not_content(repo, payload):
    assert _hook(repo, "log_event.py", payload).returncode == 0
    logs = "".join(p.read_text() for p in (repo / "logs").glob("*.jsonl"))
    assert payload["hook_event_name"] in logs
    assert SECRET not in logs


def test_log_event_keeps_the_tool_and_path(repo):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "/work/app/a.txt", "content": "x"},
    }
    _hook(repo, "log_event.py", payload)
    (entry,) = _lines(repo / "logs" / "post_tool_use.jsonl")
    assert entry["tool_name"] == "Write"
    assert entry["tool_input"] == {"file_path": "/work/app/a.txt"}


def test_guard_never_fails_on_a_bad_payload(repo):
    assert _hook(repo, "pre_tool_use.py", "garbage").returncode == 0


# --- pre_compact.py ------------------------------------------------------------------


def test_pre_compact_backs_up_the_transcript(repo, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"role": "user"}\n')
    payload = {
        "hook_event_name": "PreCompact",
        "session_id": "abc123",
        "transcript_path": str(transcript),
        "trigger": "auto",
    }
    assert _hook(repo, "pre_compact.py", payload, "--backup").returncode == 0

    # Under .claude/data/ (git-ignored by init), owner-only: it holds the whole session.
    backup_dir = repo / ".claude" / "data" / "transcript_backups"
    backups = list(backup_dir.iterdir())
    assert len(backups) == 1
    assert backups[0].read_text() == transcript.read_text()
    assert not (repo / "logs" / "transcript_backups").exists()
    if os.name != "nt":
        assert backup_dir.stat().st_mode & 0o777 == 0o700
        assert backups[0].stat().st_mode & 0o777 == 0o600


def test_pre_compact_without_a_transcript_is_harmless(repo):
    payload = {"hook_event_name": "PreCompact", "trigger": "manual"}
    assert _hook(repo, "pre_compact.py", payload, "--backup").returncode == 0
