"""Tests for `gingugu init` (the bootstrap command)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gingugu.bootstrap import CLIENT_RULES_FILES, GITIGNORE_ENTRIES, main
from gingugu.bootstrap.settings import merge_settings


def _read(path: Path) -> str:
    return path.read_text()


# --- Claude Code path ---------------------------------------------------------


def test_claude_code_writes_hooks_command_and_settings(tmp_path):
    assert main(["--path", str(tmp_path)]) == 0

    session_start = tmp_path / ".claude" / "hooks" / "session_start.py"
    stop = tmp_path / ".claude" / "hooks" / "stop.py"
    command = tmp_path / ".claude" / "commands" / "sink-the-ship.md"
    settings = tmp_path / ".claude" / "settings.json"

    assert session_start.exists()
    assert stop.exists()
    assert command.exists()
    assert settings.exists()

    # Hooks are the real product scripts, not empty stubs.
    assert "SESSION STARTUP CONTRACT" in _read(session_start)
    assert "save-discipline" in _read(stop)
    assert "Sink the Ship" in _read(command)


def test_settings_wire_both_events(tmp_path):
    main(["--path", str(tmp_path)])
    settings = json.loads(_read(tmp_path / ".claude" / "settings.json"))

    def commands_for(event):
        return [h["command"] for group in settings["hooks"][event] for h in group["hooks"]]

    assert any("session_start.py" in c for c in commands_for("SessionStart"))
    assert any("stop.py --check-memory-saves" in c for c in commands_for("Stop"))


def test_rerun_is_idempotent(tmp_path):
    main(["--path", str(tmp_path)])
    first = _read(tmp_path / ".claude" / "settings.json")
    main(["--path", str(tmp_path)])
    second = _read(tmp_path / ".claude" / "settings.json")
    assert first == second  # no duplicate hook groups on re-run


def test_merge_preserves_existing_settings(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    existing = {
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "custom.py"}]}]
        },
    }
    (claude / "settings.json").write_text(json.dumps(existing, indent=2))

    main(["--path", str(tmp_path)])
    merged = json.loads(_read(claude / "settings.json"))

    # Existing config untouched.
    assert merged["permissions"]["allow"] == ["Bash(ls:*)"]
    assert merged["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "custom.py"
    # New hooks added.
    assert "SessionStart" in merged["hooks"]
    assert "Stop" in merged["hooks"]
    # Original was backed up.
    assert (claude / "settings.json.bak").exists()


def test_existing_hook_not_duplicated(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    preset = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run .claude/hooks/session_start.py",
                        }
                    ],
                }
            ]
        }
    }
    (claude / "settings.json").write_text(json.dumps(preset))
    main(["--path", str(tmp_path)])
    merged = json.loads(_read(claude / "settings.json"))
    # SessionStart already present -> not appended a second time.
    assert len(merged["hooks"]["SessionStart"]) == 1
    # Stop still gets added.
    assert "Stop" in merged["hooks"]


# --- dry-run ------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path):
    assert main(["--path", str(tmp_path), "--dry-run"]) == 0
    assert not (tmp_path / ".claude").exists()


# --- --force ------------------------------------------------------------------


def test_force_overwrites_existing_hook(tmp_path):
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "session_start.py").write_text("OLD CONTENT")

    # Without --force, existing file is preserved.
    main(["--path", str(tmp_path)])
    assert _read(hooks / "session_start.py") == "OLD CONTENT"

    # With --force, it is replaced with the real template.
    main(["--path", str(tmp_path), "--force"])
    assert "SESSION STARTUP CONTRACT" in _read(hooks / "session_start.py")


# --- rules-file clients -------------------------------------------------------


@pytest.mark.parametrize("client,filename", CLIENT_RULES_FILES.items())
def test_rules_file_client_writes_protocol(tmp_path, client, filename):
    assert main(["--path", str(tmp_path), "--client", client]) == 0
    rules = tmp_path / filename
    assert rules.exists()
    assert "## Memory Protocol" in _read(rules)
    # No hooks for non-Claude-Code clients.
    assert not (tmp_path / ".claude").exists()


def test_bad_path_returns_error(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert main(["--path", str(missing)]) == 1


# --- .gitignore handling ------------------------------------------------------


def test_gitignore_created_when_absent(tmp_path):
    main(["--path", str(tmp_path)])
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    body = _read(gitignore)
    for entry in GITIGNORE_ENTRIES:
        assert entry in body


def test_gitignore_appends_missing_only_and_preserves_existing(tmp_path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\nlogs/\n")  # one of ours already present

    main(["--path", str(tmp_path)])
    body = _read(gitignore)

    assert "node_modules/" in body  # existing content preserved
    assert body.count("logs/") == 1  # not duplicated
    assert ".claude/data/" in body  # missing ones added


def test_gitignore_idempotent(tmp_path):
    main(["--path", str(tmp_path)])
    first = _read(tmp_path / ".gitignore")
    main(["--path", str(tmp_path)])
    assert _read(tmp_path / ".gitignore") == first


def test_rules_file_client_does_not_touch_gitignore(tmp_path):
    main(["--path", str(tmp_path), "--client", "cursor"])
    # .gitignore handling is a Claude Code concern (hook artifacts); skip for others.
    assert not (tmp_path / ".gitignore").exists()


# --- hooks must survive flags they do not own ---------------------------------


def test_stop_hook_ignores_flags_it_does_not_own(tmp_path):
    """The failure this makes impossible rather than merely detected.

    A repo's settings.json is routinely written by other tooling that appends
    its own flags to the Stop hook. ``parse_args`` would sys.exit(2) on an
    unrecognized one — and SystemExit is a BaseException, so the script's own
    ``except Exception`` cannot catch it. Claude Code reads the non-zero exit
    as a blocked stop, and the session breaks.
    """
    import subprocess
    import sys

    main(["--path", str(tmp_path)])
    stop = tmp_path / ".claude" / "hooks" / "stop.py"

    result = subprocess.run(
        [sys.executable, str(stop), "--check-memory-saves", "--notify", "--chat"],
        input='{"session_id": "abc"}',
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_init_backs_up_and_warns_on_a_stop_py_it_did_not_write(tmp_path):
    """--force must not silently clobber another tool's hook of the same name."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    foreign = hooks / "stop.py"
    foreign.write_text("# written by some other kit\nprint('not ours')\n")

    main(["--path", str(tmp_path), "--force"])

    assert (hooks / "stop.py.bak").read_text().startswith("# written by some other kit")
    assert "gingugu" in foreign.read_text()


# --- merge_settings unit ------------------------------------------------------


def test_merge_settings_reports_added_events():
    settings, added, warnings = merge_settings({})
    assert set(added) == {"SessionStart", "Stop"}
    assert warnings == []
    _, added_again, _ = merge_settings(settings)
    assert added_again == []


def _stop_wired(command: str) -> dict:
    return {
        "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": command}]}]}
    }


def test_merge_settings_warns_when_an_existing_command_carries_foreign_flags():
    """The misleading case: matching a bare filename is not "already wired".

    A repo bootstrapped by other tooling points at a same-named stop.py with
    its own flags. Reporting "already wired (no change)" tells the user their
    setup is fine when the wiring belongs to a different script entirely.
    """
    settings = _stop_wired("uv run $CLAUDE_PROJECT_DIR/.claude/hooks/stop.py --notify --chat")
    _, added, warnings = merge_settings(settings)

    assert "Stop" not in added
    assert len(warnings) == 1
    assert "--notify" in warnings[0]
    assert "--chat" in warnings[0]


def test_merge_settings_stays_quiet_when_existing_flags_are_ours():
    settings = _stop_wired(
        "uv run $CLAUDE_PROJECT_DIR/.claude/hooks/stop.py --check-memory-saves --min-tool-calls=5"
    )
    _, added, warnings = merge_settings(settings)

    assert "Stop" not in added
    assert warnings == []


# --- theme ---------------------------------------------------------------------


def test_theme_renders_banner_and_boot_text():
    from gingugu.bootstrap import theme

    out = theme.render(["Claude Code bootstrap:", "  write /x/y.py"], dry_run=False)
    assert "G I N G U G U" in out
    assert "SYST3M 4RM3D" in out
    # Tests run without a TTY, so output must be plain (no ANSI escape codes).
    assert "\033[" not in out
