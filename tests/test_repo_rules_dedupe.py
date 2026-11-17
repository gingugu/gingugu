"""The repo CLAUDE.md skips the memory protocol when ~/.claude/CLAUDE.md carries it.

Claude Code loads BOTH files into every request, so the same ~6k-char managed
block in each is paid twice per request for nothing. When the user-level file
holds the managed block, the repo CLAUDE.md gets none - and loses a stale copy
from an earlier init, backed up first. AGENTS.md is untouched by this: other
clients read it, and Claude Code does not auto-load it.
"""

from __future__ import annotations

from gingugu.bootstrap import init_claude_code
from gingugu.bootstrap.global_rules import BEGIN_MARKER, END_MARKER, render_block
from gingugu.bootstrap.repo_rules import init_repo_rules, strip_block

PROTOCOL = "## Memory Protocol\n\nLoad crow first.\n"
BLOCK = render_block(PROTOCOL)


# --- strip_block: pure ---------------------------------------------------------


def test_strip_removes_block_and_keeps_both_neighbours():
    text = f"# Repo\n\nUse black.\n\n{BLOCK}\n## Git\n\nStack PRs.\n"
    assert strip_block(text) == "# Repo\n\nUse black.\n\n## Git\n\nStack PRs.\n"


def test_strip_block_at_end_of_file_leaves_one_trailing_newline():
    text = f"# Repo\n\nUse black.\n\n{BLOCK}"
    assert strip_block(text) == "# Repo\n\nUse black.\n"


def test_strip_returns_none_when_there_is_no_managed_block():
    assert strip_block("# Repo\n\n## Memory Protocol\n\nhand-written\n") is None


# --- init_repo_rules(global_covers=True) ---------------------------------------


def test_existing_repo_block_is_removed_with_backup(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    original = f"# Repo\n\nUse black.\n\n{BLOCK}"
    claude_md.write_text(original)

    out = "\n".join(init_repo_rules(tmp_path, dry_run=False, global_covers=True))

    assert claude_md.read_text() == "# Repo\n\nUse black.\n"
    assert claude_md.with_suffix(".md.bak").read_text() == original
    assert "removed" in out and "~/.claude/CLAUDE.md" in out


def test_repo_without_block_is_left_byte_identical(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Repo\n\nUse black.\n")

    out = "\n".join(init_repo_rules(tmp_path, dry_run=False, global_covers=True))

    assert claude_md.read_text() == "# Repo\n\nUse black.\n"
    assert BEGIN_MARKER not in claude_md.read_text()
    assert not claude_md.with_suffix(".md.bak").exists()
    assert "~/.claude/CLAUDE.md" in out


def test_hand_written_repo_protocol_is_never_touched(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    original = "# Repo\n\n## Memory Protocol\n\nRun `memory_recall` first.\n"
    claude_md.write_text(original)

    init_repo_rules(tmp_path, dry_run=False, global_covers=True, adopt=True)

    assert claude_md.read_text() == original


def test_dry_run_removal_writes_nothing(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    original = f"# Repo\n\n{BLOCK}"
    claude_md.write_text(original)

    out = "\n".join(init_repo_rules(tmp_path, dry_run=True, global_covers=True))

    assert claude_md.read_text() == original
    assert not claude_md.with_suffix(".md.bak").exists()
    assert "would remove" in out


def test_create_writes_a_title_only_claude_md(tmp_path):
    init_repo_rules(tmp_path, dry_run=False, create=True, global_covers=True)

    text = (tmp_path / "CLAUDE.md").read_text()
    assert text == f"# {tmp_path.name}\n"


def test_agents_md_is_still_managed(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Agents\n")

    init_repo_rules(tmp_path, dry_run=False, global_covers=True)

    assert BEGIN_MARKER in (tmp_path / "AGENTS.md").read_text()


def test_default_keeps_old_behaviour(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Repo\n")

    init_repo_rules(tmp_path, dry_run=False)

    assert BEGIN_MARKER in (tmp_path / "CLAUDE.md").read_text()


# --- wiring through init_claude_code ---------------------------------------------


def test_init_dedupes_when_global_file_gets_the_block(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(
        "gingugu.bootstrap.global_rules.global_claude_md",
        lambda: home / ".claude" / "CLAUDE.md",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# Conventions\n\nUse black.\n")

    init_claude_code(repo, force=False, dry_run=False)

    assert BEGIN_MARKER in (home / ".claude" / "CLAUDE.md").read_text()
    assert BEGIN_MARKER not in (repo / "CLAUDE.md").read_text()


def test_init_keeps_repo_block_when_global_file_has_its_own_protocol(tmp_path, monkeypatch):
    home_md = tmp_path / "home" / ".claude" / "CLAUDE.md"
    home_md.parent.mkdir(parents=True)
    home_md.write_text("# Mine\n\n## Memory Protocol\n\nhand-written\n")  # conflict, not adopted
    monkeypatch.setattr("gingugu.bootstrap.global_rules.global_claude_md", lambda: home_md)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# Conventions\n")

    init_claude_code(repo, force=False, dry_run=False)

    text = (repo / "CLAUDE.md").read_text()
    assert BEGIN_MARKER in text and END_MARKER in text
