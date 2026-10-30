"""The checkout guard: no session moves the shared primary checkout.

Several sessions start in the same primary checkout. One session's
``git switch`` moves the branch under the others, and their uncommitted edits
ride along onto it. The guard denies branch-moving and tree-emptying git
commands there and points at a worktree; everything else passes, and a
worktree is never guarded.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from gingugu import checkout_guard, tool_hook
from gingugu.bootstrap._files import read_template
from gingugu.bootstrap.gitignore import GITIGNORE_ENTRIES


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_CHECKOUT_GUARD", raising=False)
    primary = tmp_path / "repo"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    _git(primary, "config", "user.email", "t@example.com")
    _git(primary, "config", "user.name", "t")
    (primary / "f.txt").write_text("x\n")
    _git(primary, "add", "f.txt")
    _git(primary, "commit", "-q", "-m", "init")
    worktree = primary / ".claude" / "worktrees" / "wt"
    _git(primary, "worktree", "add", "-q", str(worktree), "-b", "feature/wt")
    return primary, worktree


def _bash(cwd, command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


def _check(cwd, command):
    return checkout_guard.check(_bash(cwd, command))


# --- what is blocked in the primary checkout -----------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git switch -c feature/x",
        "git switch main",
        "git checkout -b feature/x",
        "git checkout main",
        "git rebase main",
        "git reset --hard",
        "git reset --hard origin/main",
        "git reset --keep HEAD~1",
        "git stash",
        "git stash push -m wip",
        "git stash pop",
        "git stash apply",
        "git --no-pager -c core.pager=cat checkout -b feature/x",
        "/usr/bin/git switch main",
        "git status && git checkout -b feature/x",
        "git fetch; git switch main",
        "git status\ngit switch main",
        "git status | cat && git stash",
        "git log <<'EOF'\nbody\nEOF\ngit switch main",
        "FOO=1 git switch main",
    ],
)
def test_branch_moves_in_the_primary_checkout_are_denied(repos, command):
    primary, _ = repos
    reason = _check(primary, command)
    assert reason is not None
    assert "worktree" in reason
    assert "EnterWorktree" in reason
    assert "GINGUGU_PRIMARY_OK=1" in reason


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --oneline -5",
        "git diff",
        "git fetch",
        "git pull",
        "git push",
        "git add f.txt && git commit -m msg",
        "git checkout -- f.txt",
        "git checkout HEAD -- f.txt",
        "git reset f.txt",
        "git reset HEAD f.txt",
        "git stash list",
        "git stash show",
        "git rebase --abort",
        "git worktree add .claude/worktrees/x -b feature/x",
        "git branch -a",
        "echo 'git checkout -b x'",
        "grep -rn 'git switch' .",
        "ls",
        # Prose that mentions a guarded command is not a guarded command.
        'git commit -m "docs: run git switch main; then git checkout -b x"',
        "git commit -F - <<'EOF'\nfix: guard\n\ngit switch main\ngit stash\nEOF",
        'gh pr create --body "step 1; git checkout -b feature/x"',
        'git commit -m "$(cat <<EOF\ngit reset --hard\nEOF\n)"',
    ],
)
def test_everything_else_in_the_primary_checkout_passes(repos, command):
    primary, _ = repos
    assert _check(primary, command) is None


# --- where the command runs ------------------------------------------------------


def test_a_worktree_is_never_guarded(repos):
    _, worktree = repos
    assert _check(worktree, "git switch -c feature/y") is None
    assert _check(worktree, "git reset --hard") is None


def test_cd_into_a_worktree_first_passes(repos):
    primary, worktree = repos
    assert _check(primary, f"cd {worktree} && git checkout -b feature/y") is None


def test_cd_into_the_primary_first_is_denied(repos):
    primary, worktree = repos
    assert _check(worktree, f"cd {primary} && git switch main") is not None


def test_git_dash_c_names_the_checkout(repos):
    primary, worktree = repos
    assert _check(primary, f"git -C {worktree} switch -c feature/y") is None
    assert _check(worktree, f"git -C {primary} switch main") is not None


def test_outside_a_repo_nothing_is_guarded(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_CHECKOUT_GUARD", raising=False)
    assert _check(tmp_path, "git switch main") is None


def test_the_reason_names_the_checkout(repos):
    primary, _ = repos
    assert str(primary.resolve()) in _check(primary, "git switch main")


# --- what it ignores, and the ways out --------------------------------------------


def test_other_tools_are_ignored(repos):
    primary, _ = repos
    payload = {**_bash(primary, "git switch main"), "tool_name": "Read"}
    assert checkout_guard.check(payload) is None


def test_garbage_input_is_ignored(repos):
    primary, _ = repos
    assert checkout_guard.check({"tool_name": "Bash", "tool_input": "nope"}) is None
    assert checkout_guard.check({"tool_name": "Bash", "tool_input": {"command": 5}}) is None
    assert _check(primary, "git switch 'unbalanced") is not None


def test_the_per_command_override_passes(repos):
    primary, _ = repos
    assert _check(primary, "GINGUGU_PRIMARY_OK=1 git switch main") is None


def test_the_switch_turns_it_off(repos, monkeypatch):
    primary, _ = repos
    monkeypatch.setenv("MEMORY_CHECKOUT_GUARD", "off")
    assert _check(primary, "git switch main") is None


# --- wired into gingugu hook tool --------------------------------------------------


def test_the_tool_hook_denies_it(repos, capsys, monkeypatch):
    primary, _ = repos
    monkeypatch.setenv("MEMORY_TRIPWIRES", "off")
    assert tool_hook.run(_bash(primary, "git checkout -b feature/x")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "worktree" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "checkout guard" in out["systemMessage"]


def test_the_tool_hook_is_silent_in_a_worktree(repos, capsys, monkeypatch):
    _, worktree = repos
    monkeypatch.setenv("MEMORY_TRIPWIRES", "off")
    assert tool_hook.run(_bash(worktree, "git checkout -b feature/y")) == 0
    assert capsys.readouterr().out == ""


def test_a_crashing_guard_never_breaks_the_hook(repos, capsys, monkeypatch):
    primary, _ = repos
    monkeypatch.setenv("MEMORY_TRIPWIRES", "off")

    def boom(payload):
        raise RuntimeError("bug")

    monkeypatch.setattr(checkout_guard, "check", boom)
    assert tool_hook.run(_bash(primary, "git switch main")) == 0
    assert capsys.readouterr().out == ""


# --- shipped by gingugu init ---------------------------------------------------------


def test_init_ignores_in_repo_worktrees():
    assert ".claude/worktrees/" in GITIGNORE_ENTRIES


def test_the_protocol_block_carries_the_rule():
    protocol = read_template("rules_protocol.md.tmpl")
    assert "### Concurrent sessions" in protocol
    assert "EnterWorktree" in protocol
