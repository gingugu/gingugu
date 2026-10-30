"""X20, the sender's half: a handoff from a branch is asked once to carry its range.

The receiver can only render a brief for a post that names a range. So the
``PreToolUse`` hook stops the first board post of a session that names none,
when it comes from a checkout on a branch ahead of the default branch, and the
reason is the exact line to add. The re-issued post passes; a post not about the
branch is simply re-issued unchanged. Shipped with ``gingugu hook tool``, which
``gingugu init`` already wires, so every install gets it; it runs on the
sender's machine, so a remote brain changes nothing.
"""

from __future__ import annotations

import json

import pytest

from gingugu import tool_hook
from gingugu.xray import board_nudge
from tests.test_xray_edit_hook import BASIC, _git, _repo


@pytest.fixture
def repo(tmp_path):
    r = _repo(tmp_path, BASIC)
    _git(r, "branch", "-M", "main")
    return r


@pytest.fixture
def ahead(repo):
    _git(repo, "checkout", "-q", "-b", "feature/x")
    (repo / "pkg/core.py").write_text("X = 9\n")
    _git(repo, "commit", "-q", "-am", "work")
    return repo


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "db" / "memories.db"))
    monkeypatch.delenv("MEMORY_XRAY_BOARD", raising=False)
    monkeypatch.delenv("MEMORY_TRIPWIRES", raising=False)
    return tmp_path / "db" / "hook-sessions"


def _payload(cwd, body="please review", session="cc-1", tool="mcp__gingugu__board", **extra):
    return {
        "session_id": session,
        "cwd": str(cwd),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"action": "post", "body": body, "recipient": "tyrone", **extra},
    }


# --- is_post ---------------------------------------------------------------


@pytest.mark.parametrize(
    "tool, action, want",
    [
        ("mcp__gingugu__board", "post", True),
        ("mcp__brain__board", "post", True),
        ("mcp__gingugu__board", "read", False),
        ("mcp__gingugu__board", "inbox", False),
        ("mcp__gingugu__memory_store", "post", False),
        ("board", "post", False),
        ("Bash", "post", False),
    ],
)
def test_is_post(tool, action, want):
    assert board_nudge.is_post(tool, {"action": action}) is want


def test_is_post_tolerates_bad_input():
    assert board_nudge.is_post("mcp__gingugu__board", None) is False


# --- check -----------------------------------------------------------------


def test_a_post_from_a_branch_ahead_is_asked_for_its_range(ahead, hook_env):
    reason = board_nudge.check(_payload(ahead), hook_env)
    assert reason is not None
    assert "xray: repo main..feature/x" in reason
    assert "1 commit" in reason


def test_the_line_names_the_repo_by_its_origin(ahead, hook_env):
    _git(ahead, "remote", "add", "origin", "https://github.com/Acme/Widget.git")
    reason = board_nudge.check(_payload(ahead), hook_env)
    assert reason is not None and "xray: acme/widget main..feature/x" in reason


def test_it_asks_once_per_session(ahead, hook_env):
    assert board_nudge.check(_payload(ahead), hook_env)
    assert board_nudge.check(_payload(ahead), hook_env) is None
    assert board_nudge.check(_payload(ahead, session="cc-2"), hook_env)


def test_a_post_that_already_names_a_range_passes(ahead, hook_env):
    body = "please review\nxray: main..feature/x"
    assert board_nudge.check(_payload(ahead, body=body), hook_env) is None
    # ...and did not spend the session's one ask
    assert board_nudge.check(_payload(ahead), hook_env)


def test_silent_on_the_default_branch_or_with_nothing_ahead(repo, hook_env):
    assert board_nudge.check(_payload(repo), hook_env) is None
    _git(repo, "checkout", "-q", "-b", "feature/empty")
    assert board_nudge.check(_payload(repo, session="cc-2"), hook_env) is None


def test_silent_on_a_detached_head(ahead, hook_env):
    _git(ahead, "checkout", "-q", "--detach")
    assert board_nudge.check(_payload(ahead), hook_env) is None


def test_master_is_a_default_branch_too(tmp_path, hook_env):
    r = _repo(tmp_path / "m", BASIC)
    _git(r, "branch", "-M", "master")
    _git(r, "checkout", "-q", "-b", "fix/y")
    (r / "pkg/leaf.py").write_text("Y = 3\n")
    _git(r, "commit", "-q", "-am", "fix")
    reason = board_nudge.check(_payload(r), hook_env)
    assert reason is not None and "xray: repo master..fix/y" in reason


def test_silent_outside_a_repo_without_a_session_or_switched_off(
    ahead, hook_env, tmp_path, monkeypatch
):
    (tmp_path / "plain").mkdir()
    assert board_nudge.check(_payload(tmp_path / "plain"), hook_env) is None
    assert board_nudge.check(_payload(ahead, session=""), hook_env) is None
    monkeypatch.setenv("MEMORY_XRAY_BOARD", "off")
    assert board_nudge.check(_payload(ahead), hook_env) is None


def test_a_branch_name_is_quoted_if_it_needs_it(repo, hook_env):
    _git(repo, "checkout", "-q", "-b", "feat/ok")
    (repo / "pkg/leaf.py").write_text("Y = 4\n")
    _git(repo, "commit", "-q", "-am", "x")
    reason = board_nudge.check(_payload(repo), hook_env)
    assert reason is not None and "xray: repo main..feat/ok" in reason


# --- wired into gingugu hook tool ------------------------------------------------


def test_the_hook_denies_the_first_post_and_passes_the_reissue(ahead, hook_env, capsys):
    assert tool_hook.run(_payload(ahead)) == 0
    out = json.loads(capsys.readouterr().out)
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert "xray: repo main..feature/x" in spec["permissionDecisionReason"]
    assert "board post" in out["systemMessage"]
    tool_hook.run(_payload(ahead))
    assert capsys.readouterr().out == ""


def test_the_hook_never_stops_other_board_actions(ahead, hook_env, capsys):
    p = _payload(ahead)
    p["tool_input"]["action"] = "inbox"
    tool_hook.run(p)
    assert capsys.readouterr().out == ""


def test_a_crashing_nudge_never_breaks_the_hook(ahead, hook_env, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nudge bug")

    monkeypatch.setattr(board_nudge, "check", boom)
    assert tool_hook.run(_payload(ahead)) == 0
    assert capsys.readouterr().out == ""
