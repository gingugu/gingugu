"""Tests for `gingugu init` (the bootstrap command)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gingugu.bootstrap import CLIENT_RULES_FILES, GITIGNORE_ENTRIES, main
from gingugu.bootstrap.settings import declared_flags, foreign_flags, merge_settings

STOP_CMD_WITH_EXTRA_FLAGS = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks/stop.py --notify --chat"

# A hook that legitimately accepts more than our template does — the shape of a
# real customized kit hook.
RICHER_STOP_PY = """# a repo's own richer stop hook
import argparse
p = argparse.ArgumentParser()
p.add_argument("--chat", action="store_true")
p.add_argument("--notify", action="store_true")
p.add_argument("--check-memory-saves", action="store_true")
"""


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
    assert set(added) == {"SessionStart", "Stop", "UserPromptSubmit"}
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


# --- the flag check reads the script on disk, not a hardcoded list -------------


def test_declared_flags_reads_the_installed_script(tmp_path):
    script = tmp_path / "stop.py"
    script.write_text(RICHER_STOP_PY)
    assert declared_flags(script) == {"--chat", "--notify", "--check-memory-saves"}


def test_declared_flags_is_none_when_unreadable(tmp_path):
    assert declared_flags(tmp_path / "nope.py") is None


def test_foreign_flags_defers_to_the_installed_script(tmp_path):
    """False positive fixed: a richer same-named hook is not "a different script".

    Regression: the check compared against a hardcoded list of OUR template's
    flags, so a repo running its own hook that genuinely accepts `--chat` and
    `--notify` was reported as misconfigured when it was correct.
    """
    script = tmp_path / "stop.py"
    script.write_text(RICHER_STOP_PY)
    assert foreign_flags(STOP_CMD_WITH_EXTRA_FLAGS, "stop.py", script=script) == []


def test_foreign_flags_still_reports_genuinely_orphaned_flags(tmp_path):
    """The real hazard: the script on disk does NOT declare the wired flags."""
    script = tmp_path / "stop.py"
    script.write_text('import argparse\np.add_argument("--check-memory-saves")\n')
    assert foreign_flags(STOP_CMD_WITH_EXTRA_FLAGS, "stop.py", script=script) == [
        "--notify",
        "--chat",
    ]


def test_foreign_flags_falls_back_when_no_script_is_available():
    assert foreign_flags(STOP_CMD_WITH_EXTRA_FLAGS, "stop.py") == ["--notify", "--chat"]


def test_merge_settings_is_quiet_when_the_installed_hook_declares_the_flags(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "stop.py").write_text(RICHER_STOP_PY)

    _, added, warnings = merge_settings(_stop_wired(STOP_CMD_WITH_EXTRA_FLAGS), hooks_dir=hooks)

    assert "Stop" not in added
    assert warnings == [], "a hook that accepts the flags must not be called foreign"


# --- --force must never destroy a customized hook without a backup ------------


def test_force_backs_up_a_customized_hook_that_merely_mentions_gingugu(tmp_path):
    """Regression for real data loss.

    The signature used to be the bare word "gingugu", which every gingugu-aware
    hook contains — the MCP tool names are `mcp__gingugu__*`. A heavily
    customized local hook was therefore treated as ours and overwritten by
    `--force` with NO backup; only a clean git tree saved it.
    """
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    customized = 'MEMORY_WRITE_TOOLS = {"mcp__gingugu__memory_store"}\n# my own extras\n'
    (hooks / "stop.py").write_text(customized)

    main(["--path", str(tmp_path), "--force"])

    assert (
        hooks / "stop.py.bak"
    ).read_text() == customized, "a customized hook must be backed up before --force replaces it"
    assert "gingugu-init:managed-file" in (hooks / "stop.py").read_text()


def test_reforce_backs_up_local_edits_to_our_own_marked_file(tmp_path):
    """Regression for the second round of real data loss.

    This assertion used to run the other way - it asserted that re-running
    ``--force`` over our own marked file wrote NO backup, and it was green in CI
    the whole time the behavior was destroying files. Conditioning the backup on
    the marker being ABSENT meant the net vanished the moment it first worked:
    every repo initialized after the marker shipped had no protection left.
    """
    hooks = tmp_path / ".claude" / "hooks"
    main(["--path", str(tmp_path)])

    edited = _read(hooks / "stop.py") + "\n# MY PRECIOUS LOCAL EDIT\n"
    (hooks / "stop.py").write_text(edited)

    main(["--path", str(tmp_path), "--force"])

    assert (
        hooks / "stop.py.bak"
    ).read_text() == edited, "local edits to a managed file must survive --force as a .bak"
    assert "MY PRECIOUS LOCAL EDIT" not in _read(hooks / "stop.py")


def test_reforce_over_an_untouched_file_makes_no_backup(tmp_path):
    """No change on disk means nothing to lose - stay quiet rather than litter."""
    main(["--path", str(tmp_path)])
    main(["--path", str(tmp_path), "--force"])
    assert not (tmp_path / ".claude" / "hooks" / "stop.py.bak").exists()


@pytest.mark.parametrize("client,filename", CLIENT_RULES_FILES.items())
def test_force_backs_up_a_hand_authored_rules_file(tmp_path, client, filename):
    """A rules file is the user's from line one - it was never ours to replace.

    This path had no backup at all, on any branch: ``--force`` wrote the template
    straight over whatever the user had written.
    """
    rules = tmp_path / filename
    handwritten = "# MY HAND-WRITTEN RULES\n- never delete prod\n"
    rules.write_text(handwritten)

    main(["--path", str(tmp_path), "--client", client, "--force"])

    assert (
        tmp_path / f"{filename}.bak"
    ).read_text() == handwritten, "a hand-authored rules file must be backed up before --force"
    assert "## Memory Protocol" in _read(rules)


def test_dry_run_force_writes_nothing_at_all(tmp_path):
    """--dry-run must not create the backup either; it reports, it does not act."""
    hooks = tmp_path / ".claude" / "hooks"
    main(["--path", str(tmp_path)])
    (hooks / "stop.py").write_text("# local edit\n")

    main(["--path", str(tmp_path), "--force", "--dry-run"])

    assert _read(hooks / "stop.py") == "# local edit\n"
    assert not (hooks / "stop.py.bak").exists()


# --- theme ---------------------------------------------------------------------


def test_theme_renders_banner_and_boot_text():
    from gingugu.bootstrap import theme

    out = theme.render(["Claude Code bootstrap:", "  write /x/y.py"], dry_run=False)
    assert "G I N G U G U" in out
    assert "SYST3M 4RM3D" in out
    # Tests run without a TTY, so output must be plain (no ANSI escape codes).
    assert "\033[" not in out


def test_startup_contract_does_not_invite_namespace_inference(tmp_path):
    """The contract must not tell the agent to guess the workspace.

    Regression for 2026-08-04: the old text said "Append any other workspace
    repos to the list", but a SessionStart hook only ever receives ``cwd`` —
    it has no workspace roster. The agent filled the gap from "Additional
    working directories", a permission allowlist, and loaded five namespaces
    at startup instead of two.
    """
    assert main(["--path", str(tmp_path)]) == 0
    contract = _read(tmp_path / ".claude" / "hooks" / "session_start.py")

    assert "Append any" not in contract
    assert "other workspace repos" not in contract
    # The floor is derived from cwd, and the allowlist is named as off-limits.
    assert "is the floor, always" in contract
    assert "permission allowlist, not the workspace" in contract
