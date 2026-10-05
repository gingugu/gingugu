"""`gingugu harness --migrate` / `--prune` - moving a repo off an older hook kit.

Plain ``harness`` only adds. On a repo that already runs a per-event logging
kit that leaves the old scripts wired beside ``log_event`` and keeps the old
copies of our own hooks. ``--migrate`` replaces our hooks, unwires the legacy
loggers, and retires their files behind exit-0 stubs - a running Claude Code
session still calls the hooks it started with, and a missing script blocks the
prompt. ``--prune`` (after a restart) removes the stubs and the retired files.
Repo-specific agents and skills are never touched.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from gingugu.bootstrap._files import read_template
from gingugu.bootstrap.harness import main

RUN = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks"
RETIRED = "gingugu-harness:retired"
LEGACY = ("post_tool_use.py", "user_prompt_submit.py", "notification.py")


def _group(*commands, matcher=""):
    return {"matcher": matcher, "hooks": [{"type": "command", "command": c} for c in commands]}


@pytest.fixture
def repo(tmp_path):
    """A repo running the older kit, plus a custom hook and its own minion and skill."""
    path = tmp_path / "legacy-repo"
    hooks = path / ".claude" / "hooks"
    (hooks / "utils" / "tts").mkdir(parents=True)
    (hooks / "utils" / "tts" / "speak.py").write_text("# legacy tts\n")
    for name in LEGACY:
        (hooks / name).write_text(f"# legacy {name}\n")
    (hooks / "stop.py").write_text("# old stop\n")
    (hooks / "pre_tool_use.py").write_text("# old guard\n")
    (hooks / "my_lint.py").write_text("# the user's own hook\n")
    (path / ".claude" / "agents").mkdir()
    (path / ".claude" / "agents" / "security-reviewer.md").write_text("# mine\n")
    (path / ".claude" / "skills" / "creating-pr").mkdir(parents=True)
    (path / ".claude" / "skills" / "creating-pr" / "SKILL.md").write_text("mine\n")
    settings = {
        "hooks": {
            "PostToolUse": [_group(f"{RUN}/post_tool_use.py")],
            "UserPromptSubmit": [
                _group(f"{RUN}/user_prompt_submit.py --log-only", f"{RUN}/user_prompt_recall.py")
            ],
            "Notification": [_group(f"{RUN}/notification.py --notify")],
            "Stop": [_group(f"{RUN}/stop.py --chat --notify --check-memory-saves")],
            "PreToolUse": [_group(f"{RUN}/pre_tool_use.py"), _group(f"{RUN}/my_lint.py")],
        }
    }
    (path / ".claude" / "settings.json").write_text(json.dumps(settings))
    return path


def _settings(repo):
    return json.loads((repo / ".claude" / "settings.json").read_text())


def _commands(settings, event):
    return [h["command"] for g in settings["hooks"].get(event, []) for h in g["hooks"]]


def _all_commands(settings):
    return [c for event in settings["hooks"] for c in _commands(settings, event)]


def _snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- --migrate ---------------------------------------------------------------------


def test_migrate_replaces_our_own_hooks_and_backs_up_the_old(repo):
    assert main(["--path", str(repo), "--migrate"]) == 0
    hooks = repo / ".claude" / "hooks"
    assert (hooks / "pre_tool_use.py").read_text() == read_template(
        "harness/hooks/pre_tool_use.py.tmpl"
    )
    assert (hooks / "stop.py").read_text() == read_template("stop.py.tmpl")
    assert (hooks / "stop.py.bak").read_text() == "# old stop\n"


def test_migrate_keeps_the_repos_own_minions_and_skills(repo):
    main(["--path", str(repo), "--migrate"])
    claude = repo / ".claude"
    assert (claude / "agents" / "security-reviewer.md").read_text() == "# mine\n"
    assert (claude / "skills" / "creating-pr" / "SKILL.md").read_text() == "mine\n"
    assert (claude / "agents" / "ai-docs-auditor.md").exists()  # missing ones are added


def test_migrate_unwires_the_legacy_loggers_in_favour_of_log_event(repo):
    main(["--path", str(repo), "--migrate"])
    settings = _settings(repo)
    for name in LEGACY:
        assert not any(name in c for c in _all_commands(settings)), name
    for event in ("PostToolUse", "UserPromptSubmit", "Notification"):
        assert sum("log_event.py" in c for c in _commands(settings, event)) == 1, event
    assert f"{RUN}/user_prompt_recall.py" in _commands(settings, "UserPromptSubmit")


def test_migrate_resets_our_own_commands_to_their_canonical_flags(repo):
    main(["--path", str(repo), "--migrate"])
    assert _commands(_settings(repo), "Stop") == [f"{RUN}/stop.py --check-memory-saves"]


def test_migrate_leaves_hooks_it_does_not_know_alone(repo):
    main(["--path", str(repo), "--migrate"])
    assert f"{RUN}/my_lint.py" in _commands(_settings(repo), "PreToolUse")
    assert (repo / ".claude" / "hooks" / "my_lint.py").read_text() == "# the user's own hook\n"


def test_migrate_retires_legacy_scripts_behind_exit_zero_stubs(repo):
    main(["--path", str(repo), "--migrate"])
    hooks = repo / ".claude" / "hooks"
    for name in LEGACY:
        assert RETIRED in (hooks / name).read_text()
        assert (hooks / "retired" / name).read_text() == f"# legacy {name}\n"
        result = subprocess.run(
            [sys.executable, str(hooks / name), "--log-only"],
            input='{"hook_event_name": "PostToolUse"}',
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (result.returncode, result.stdout) == (0, "")
    assert (hooks / "retired" / "utils" / "tts" / "speak.py").exists()
    assert not (hooks / "utils").exists()


def test_migrate_twice_changes_nothing(repo):
    main(["--path", str(repo), "--migrate"])
    before = _snapshot(repo)
    main(["--path", str(repo), "--migrate"])
    assert _snapshot(repo) == before


def test_migrate_dry_run_writes_nothing(repo):
    before = _snapshot(repo)
    assert main(["--path", str(repo), "--migrate", "--dry-run"]) == 0
    assert _snapshot(repo) == before


def test_plain_harness_never_unwires_anything(repo):
    main(["--path", str(repo)])
    settings = _settings(repo)
    assert f"{RUN}/post_tool_use.py" in _commands(settings, "PostToolUse")
    assert (repo / ".claude" / "hooks" / "post_tool_use.py").read_text().startswith("# legacy")


# --- --prune -------------------------------------------------------------------------


def test_prune_removes_the_stubs_and_the_retired_files(repo):
    main(["--path", str(repo), "--migrate"])
    assert main(["--path", str(repo), "--prune"]) == 0
    hooks = repo / ".claude" / "hooks"
    for name in LEGACY:
        assert not (hooks / name).exists()
    assert not (hooks / "retired").exists()
    assert (hooks / "my_lint.py").exists()
    assert (hooks / "log_event.py").exists()


def test_prune_keeps_a_stub_that_is_still_wired(repo):
    main(["--path", str(repo), "--migrate"])
    settings = _settings(repo)
    settings["hooks"]["Notification"].append(_group(f"{RUN}/notification.py"))
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings))
    main(["--path", str(repo), "--prune"])
    assert (repo / ".claude" / "hooks" / "notification.py").exists()
    assert not (repo / ".claude" / "hooks" / "post_tool_use.py").exists()


def test_prune_installs_nothing(tmp_path):
    repo = tmp_path / "fresh"
    repo.mkdir()
    assert main(["--path", str(repo), "--prune"]) == 0
    assert list(repo.iterdir()) == []


def test_migrate_and_prune_together_is_refused(repo):
    with pytest.raises(SystemExit) as exit_info:  # argparse: mutually exclusive
        main(["--path", str(repo), "--migrate", "--prune"])
    assert exit_info.value.code == 2
