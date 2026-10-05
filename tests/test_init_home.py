"""`gingugu init` aimed at the home directory.

``~/.claude/settings.json`` is Claude Code's user-level settings file, loaded in
every project. Hooks wired there as ``$CLAUDE_PROJECT_DIR/.claude/hooks/...``
resolve inside whatever project is open, and in a project without its own
``init`` the script is missing: ``uv`` exits 2, which Claude Code treats as a
block for UserPromptSubmit and PreToolUse. So from home, init does the
user-level steps only and points at per-project ``init``.
"""

from __future__ import annotations

import json

import pytest

from gingugu.bootstrap import main
from gingugu.bootstrap.settings import MINION_ALLOW


def _home(user_settings):
    """The sandboxed home whose ``.claude/settings.json`` is the user-level file."""
    return user_settings.parent.parent


def test_home_target_wires_no_hooks(sandboxed_user_settings):
    home = _home(sandboxed_user_settings)
    assert main(["--path", str(home)]) == 0

    settings = json.loads(sandboxed_user_settings.read_text())
    assert "hooks" not in settings


def test_home_target_writes_no_project_files(sandboxed_user_settings):
    home = _home(sandboxed_user_settings)
    main(["--path", str(home)])

    assert not (home / ".claude" / "hooks").exists()
    assert not (home / ".claude" / "skills").exists()
    assert not (home / ".gitignore").exists()


def test_home_target_still_does_the_user_level_steps(sandboxed_user_settings):
    home = _home(sandboxed_user_settings)
    main(["--path", str(home)])

    settings = json.loads(sandboxed_user_settings.read_text())
    assert MINION_ALLOW in settings["permissions"]["allow"]


def test_home_target_says_to_init_each_project(sandboxed_user_settings, capsys):
    home = _home(sandboxed_user_settings)
    main(["--path", str(home)])

    out = capsys.readouterr().out
    assert "home directory" in out
    assert "inside each project" in out


def test_home_target_dry_run_writes_nothing(sandboxed_user_settings, capsys):
    home = _home(sandboxed_user_settings)
    assert main(["--path", str(home), "--dry-run"]) == 0

    assert not sandboxed_user_settings.exists()
    assert not (home / ".claude" / "hooks").exists()
    assert "inside each project" in capsys.readouterr().out


def test_a_symlink_to_home_is_still_home(tmp_path, sandboxed_user_settings):
    link = tmp_path / "home-link"
    link.symlink_to(_home(sandboxed_user_settings), target_is_directory=True)
    main(["--path", str(link)])

    assert not (link / ".claude" / "hooks").exists()


def test_a_differently_cased_home_is_still_home(sandboxed_user_settings):
    home = _home(sandboxed_user_settings)
    swapped = home.parent / home.name.swapcase()
    if not swapped.exists():
        pytest.skip("case-sensitive filesystem")
    main(["--path", str(swapped)])

    assert not (home / ".claude" / "hooks").exists()


def test_a_project_target_still_gets_its_hooks(tmp_path, sandboxed_user_settings):
    project = tmp_path / "repo"
    project.mkdir()
    main(["--path", str(project)])

    assert (project / ".claude" / "hooks" / "user_prompt_recall.py").exists()
    assert "hooks" in json.loads((project / ".claude" / "settings.json").read_text())
    assert "hooks" not in json.loads(sandboxed_user_settings.read_text())
