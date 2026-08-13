"""Tests for ``gingugu init --global`` (user-level rules file management).

The invariant this file exists to protect: **nothing the user wrote is ever
lost.** The user-level rules file is hand-authored, loaded in every session, and
carries identity/workflow rules unrelated to memory. So the only bytes this tool
may ever rewrite are the ones it put between its own markers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gingugu.bootstrap import main
from gingugu.bootstrap.global_rules import (
    BEGIN_MARKER,
    END_MARKER,
    global_claude_md,
    init_global_rules,
    merge_block,
    render_block,
)

PROTOCOL = "## Memory Protocol\n\nLoad crow first.\n"
# A previously-installed (now outdated) protocol. Kept as a module constant
# because escapes inside an f-string expression need Python 3.12+ and this
# project supports 3.11.
OLD_PROTOCOL = "## Memory Protocol\n\nOLD RULES.\n"

# A hand-authored global file: identity and workflow rules that have nothing to
# do with memory, and must survive every operation untouched.
USER_PROSE = """# My Global Rules

## Identity

You are a pirate robot. I am the captain.

## Git Workflow

- Run `git status` before committing.
"""


# --- merge_block: the decision table ------------------------------------------


def test_empty_file_gets_the_block():
    new_text, status = merge_block("", PROTOCOL)
    assert status == "appended"
    assert new_text == render_block(PROTOCOL)


def test_unrelated_prose_is_appended_to_not_replaced():
    new_text, status = merge_block(USER_PROSE, PROTOCOL)
    assert status == "appended"
    # Every original byte survives, in order, at the front.
    assert new_text.startswith(USER_PROSE)
    assert BEGIN_MARKER in new_text and END_MARKER in new_text


def test_existing_managed_block_is_refreshed_in_place():
    original = USER_PROSE + "\n" + render_block(OLD_PROTOCOL)
    new_text, status = merge_block(original, PROTOCOL)

    assert status == "updated"
    assert "OLD RULES" not in new_text
    assert "Load crow first." in new_text
    # The user's own prose is byte-identical and still first.
    assert new_text.startswith(USER_PROSE)


def test_refresh_preserves_prose_written_after_the_block():
    trailing = "\n## My Own Section\n\nKeep me.\n"
    original = USER_PROSE + "\n" + render_block(OLD_PROTOCOL) + trailing
    new_text, status = merge_block(original, PROTOCOL)

    assert status == "updated"
    assert new_text.endswith(trailing)
    assert "Keep me." in new_text


def test_second_run_is_idempotent():
    once, _ = merge_block(USER_PROSE, PROTOCOL)
    assert once is not None
    twice, status = merge_block(once, PROTOCOL)
    assert status == "current"
    assert twice is None, "an unchanged file must not be rewritten"


def test_unmanaged_memory_protocol_is_not_silently_duplicated():
    """The drift case: a hand-written protocol with no markers."""
    hand_written = USER_PROSE + "\n## Memory Protocol (Gingugu)\n\nLoad crow FIRST.\n"
    new_text, status = merge_block(hand_written, PROTOCOL)

    assert status == "conflict"
    assert new_text is None


def test_no_flag_can_bypass_the_unmanaged_protocol_guard():
    """`merge_block` takes no force parameter, by design.

    Regression: `--force` (meaning "overwrite the repo hooks I own") used to also
    authorize appending over a hand-written protocol. A real run aimed at a
    repo's hooks silently appended a duplicate protocol to a user-level file. The
    two decisions are different sizes and must not share a flag.
    """
    with pytest.raises(TypeError):
        merge_block("x", PROTOCOL, force=True)  # type: ignore[call-arg]

    hand_written = USER_PROSE + "\n## Memory Protocol (Gingugu)\n\nLoad crow FIRST.\n"
    assert merge_block(hand_written, PROTOCOL) == (None, "conflict")


def test_repo_force_does_not_append_to_a_hand_written_global_file(tmp_path, monkeypatch):
    """End-to-end regression for the accident: `init --force` in a repo.

    The repo's own files must be overwritten as asked, while a user-level file
    carrying its own protocol is left byte-identical.
    """
    home = tmp_path / "home" / ".claude"
    home.mkdir(parents=True)
    global_file = home / "CLAUDE.md"
    hand_written = USER_PROSE + "\n## Memory Protocol (Gingugu)\n\nMine, hand-tuned.\n"
    global_file.write_text(hand_written)
    monkeypatch.setattr("gingugu.bootstrap.global_rules.global_claude_md", lambda: global_file)

    repo = tmp_path / "repo"
    repo.mkdir()
    assert main(["--path", str(repo)]) == 0
    hook = repo / ".claude" / "hooks" / "stop.py"
    hook.write_text("# my own edits\n")

    assert main(["--path", str(repo), "--force"]) == 0

    assert hook.read_text() != "# my own edits\n", "--force must overwrite repo files"
    assert (
        global_file.read_text() == hand_written
    ), "--force must NOT append to a hand-written user-level file"
    assert not (home / "CLAUDE.md.bak").exists()


# --- init_global_rules: the filesystem side -----------------------------------


def test_writes_a_missing_file_and_creates_parents(tmp_path):
    target = tmp_path / ".claude" / "CLAUDE.md"
    out = "\n".join(init_global_rules(dry_run=False, path=target))

    assert target.exists()
    assert BEGIN_MARKER in target.read_text()
    assert "appended" in out


def test_dry_run_writes_nothing(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(USER_PROSE)
    out = "\n".join(init_global_rules(dry_run=True, path=target))

    assert target.read_text() == USER_PROSE
    assert "would append" in out


def test_refresh_leaves_a_backup_but_append_does_not(tmp_path):
    target = tmp_path / "CLAUDE.md"
    backup = tmp_path / "CLAUDE.md.bak"

    target.write_text(USER_PROSE)
    init_global_rules(dry_run=False, path=target)
    assert not backup.exists(), "appending risks nothing, so it needs no backup"
    fresh = target.read_text()

    # Stale the managed block. Done by injecting inside the markers rather than
    # editing known protocol text, so the test does not depend on the shipped
    # template's wording.
    target.write_text(fresh.replace(END_MARKER, f"STALE LINE\n{END_MARKER}"))
    init_global_rules(dry_run=False, path=target)

    assert backup.exists()
    assert "STALE LINE" in backup.read_text(), "the backup holds the pre-refresh file"
    assert "STALE LINE" not in target.read_text(), "the refresh dropped the stale line"
    assert target.read_text() == fresh, "a refresh restores the canonical block exactly"


def test_conflict_warns_and_writes_nothing(tmp_path):
    target = tmp_path / "CLAUDE.md"
    hand_written = USER_PROSE + "\n## Memory Protocol (Gingugu)\n\nMine.\n"
    target.write_text(hand_written)

    out = "\n".join(init_global_rules(dry_run=False, path=target))

    assert target.read_text() == hand_written
    assert "WARNING" in out
    assert BEGIN_MARKER in out, "the warning must show how to opt in"


def test_default_path_is_the_user_level_claude_md():
    assert global_claude_md() == Path.home() / ".claude" / "CLAUDE.md"


# --- CLI wiring ---------------------------------------------------------------


def test_claude_code_bootstrap_manages_the_global_file(tmp_path, monkeypatch):
    """The global rules file is part of the Claude Code bootstrap, not an opt-in."""
    home = tmp_path / "home"
    monkeypatch.setattr(
        "gingugu.bootstrap.global_rules.global_claude_md",
        lambda: home / ".claude" / "CLAUDE.md",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    assert main(["--path", str(repo)]) == 0

    # Repo artifacts, as before.
    assert (repo / ".claude" / "hooks" / "session_start.py").exists()
    assert (repo / ".claude" / "settings.json").exists()
    # Plus the user-level file, with no flag passed.
    assert BEGIN_MARKER in (home / ".claude" / "CLAUDE.md").read_text()


def test_dry_run_bootstrap_leaves_the_global_file_alone(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(
        "gingugu.bootstrap.global_rules.global_claude_md",
        lambda: home / ".claude" / "CLAUDE.md",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    assert main(["--path", str(repo), "--dry-run"]) == 0
    assert not home.exists(), "--dry-run must not create anything in the home dir"


@pytest.mark.parametrize("client", ["windsurf", "cursor", "cline"])
def test_non_claude_clients_do_not_touch_the_global_file(tmp_path, monkeypatch, client):
    """Only the Claude Code path owns ~/.claude; other clients' paths are unverified."""
    home = tmp_path / "home"
    monkeypatch.setattr(
        "gingugu.bootstrap.global_rules.global_claude_md",
        lambda: home / ".claude" / "CLAUDE.md",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    assert main(["--path", str(repo), "--client", client]) == 0
    assert not home.exists()


def test_global_flag_is_not_a_thing(tmp_path):
    """A --global flag would imply the step is optional. It isn't."""
    with pytest.raises(SystemExit):
        main(["--path", str(tmp_path), "--global"])
