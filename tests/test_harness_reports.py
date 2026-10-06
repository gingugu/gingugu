"""``gingugu harness`` reports: a dry run says what the real run then does.

An end-to-end run against a real older hook kit turned up four reports that
disagreed with what happened on disk:

- ``--migrate --dry-run`` kept ``hooks/utils`` because the kit's own ``stop.py``
  mentions it, though the real run replaces that ``stop.py`` first and moves them;
- on a repo with no ``CLAUDE.md`` the dry run said there was no rules file to
  touch, then the real run created one and called it "your existing rules";
- a forced rewrite of identical bytes reported ``overwrite``;
- a dry run said ``would write`` over an existing file, and that the file was
  already "Backed up".
"""

from __future__ import annotations

import json
import os
import re

import pytest

from gingugu.bootstrap import main as init_main
from gingugu.bootstrap.harness import main

RUN = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks"


def _kit_repo(tmp_path, *, own_hook: str | None = None):
    """An older kit: one legacy script, its utils, and a stop.py that imports them."""
    repo = tmp_path / "kit-repo"
    hooks = repo / ".claude" / "hooks"
    (hooks / "utils" / "tts").mkdir(parents=True)
    (hooks / "utils" / "tts" / "tts_queue.py").write_text("# kit tts\n")
    (hooks / "post_tool_use.py").write_text(
        '# kit post_tool_use.py\nlog_path = os.path.join("logs", "post_tool_use.json")\n'
    )
    (hooks / "stop.py").write_text("from utils.tts import tts_queue\n")
    if own_hook:
        (hooks / own_hook).write_text("from utils.tts import tts_queue\n")
    settings = {
        "hooks": {
            "PostToolUse": [{"hooks": [{"type": "command", "command": f"{RUN}/post_tool_use.py"}]}]
        }
    }
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings))
    return repo


def _run(argv, capsys) -> str:
    assert main(argv) == 0
    return capsys.readouterr().out


# --- the utils move -------------------------------------------------------------------


def test_migrate_dry_run_reports_the_utils_move_the_real_run_makes(tmp_path, capsys):
    repo = _kit_repo(tmp_path)
    dry = _run(["--path", str(repo), "--migrate", "--dry-run"], capsys)
    real = _run(["--path", str(repo), "--migrate"], capsys)

    assert "would move 1 kit file(s) from hooks/utils" in dry
    assert "hooks/utils:" not in dry
    assert "moved 1 kit file(s) from hooks/utils" in real
    assert (repo / ".claude" / "hooks" / "retired" / "utils" / "tts" / "tts_queue.py").exists()


def test_a_users_own_hook_that_needs_utils_still_keeps_them(tmp_path, capsys):
    repo = _kit_repo(tmp_path, own_hook="my_hook.py")
    dry = _run(["--path", str(repo), "--migrate", "--dry-run"], capsys)
    real = _run(["--path", str(repo), "--migrate"], capsys)

    for out in (dry, real):
        assert "hooks/utils: my_hook.py mentions it and may still need it" in out
        assert "kit file(s) from hooks/utils" not in out
    assert (repo / ".claude" / "hooks" / "utils" / "tts" / "tts_queue.py").exists()


def test_two_blockers_read_as_plural(tmp_path, capsys):
    repo = _kit_repo(tmp_path, own_hook="my_hook.py")
    (repo / ".claude" / "hooks" / "other.py").write_text("import utils\n")
    out = _run(["--path", str(repo), "--migrate", "--dry-run"], capsys)
    assert "hooks/utils: my_hook.py, other.py mention it" in out


# --- a repo with no CLAUDE.md ---------------------------------------------------------


def test_harness_on_a_repo_without_claude_md_dry_run_matches_real(tmp_path, capsys):
    repo = tmp_path / "bare"
    repo.mkdir()
    dry = _run(["--path", str(repo), "--dry-run"], capsys)
    assert not (repo / "CLAUDE.md").exists()
    real = _run(["--path", str(repo)], capsys)

    claude = repo / "CLAUDE.md"
    for out, verb in ((dry, "would append"), (real, "appended")):
        assert "none present" not in out
        assert re.search(rf"{verb}\s+{re.escape(str(claude))}  \(new file\)", out)
        assert "below your existing rules" not in out
    text = claude.read_text()
    assert text.startswith("# bare\n")
    assert "BEGIN GINGUGU MEMORY PROTOCOL" in text and "BEGIN GINGUGU HARNESS" in text


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_dangling_claude_md_link_is_never_written_through(tmp_path, capsys):
    repo = tmp_path / "linked"
    repo.mkdir()
    outside = tmp_path / "elsewhere" / "CLAUDE.md"
    (repo / "CLAUDE.md").symlink_to(outside)

    for argv in (["--dry-run"], []):
        out = _run(["--path", str(repo), *argv], capsys)
        assert "a symlink; never written through" in out
        assert "harness block" not in out
    assert not outside.exists() and not outside.parent.exists()


def test_plain_init_still_creates_no_claude_md(tmp_path, capsys):
    assert init_main(["--path", str(tmp_path)]) == 0
    assert "none present" in capsys.readouterr().out
    assert not (tmp_path / "CLAUDE.md").exists()


# --- forced rewrites ------------------------------------------------------------------


def test_migrate_rerun_reports_no_change_for_identical_hooks(tmp_path, capsys):
    repo = _kit_repo(tmp_path)
    _run(["--path", str(repo), "--migrate"], capsys)
    baks = sorted(p.name for p in (repo / ".claude" / "hooks").glob("*.bak"))

    again = _run(["--path", str(repo), "--migrate"], capsys)
    assert not re.search(r"overwrite\s+\S+\.py", again)
    assert re.search(r"no change\s+\S+[\\/]stop\.py", again)
    assert sorted(p.name for p in (repo / ".claude" / "hooks").glob("*.bak")) == baks


def test_dry_run_over_an_existing_file_says_would_overwrite(tmp_path, capsys):
    repo = _kit_repo(tmp_path)
    dry = _run(["--path", str(repo), "--migrate", "--dry-run"], capsys)

    assert re.search(r"would overwrite\s+\S+[\\/]stop\.py", dry)
    assert re.search(r"would write\s+\S+[\\/]log_event\.py", dry)  # absent: still a write


def test_dry_run_warning_does_not_claim_a_backup_was_made(tmp_path, capsys):
    repo = _kit_repo(tmp_path)
    dry = _run(["--path", str(repo), "--migrate", "--dry-run"], capsys)
    real = _run(["--path", str(repo), "--migrate"], capsys)

    assert "Backed up to stop.py.bak" not in dry
    assert "Would be backed up to stop.py.bak" in dry
    assert "Backed up to stop.py.bak" in real
