"""Tests for board item #1: ``--adopt`` and repo-root CLAUDE.md/AGENTS.md management.

Two things are new here, both built on the existing ``merge_block`` engine:
- ``--adopt`` wraps an existing hand-written protocol section in the managed
  markers (heading-bounded), so a file that used to hit a permanent "conflict"
  skip can opt in to management.
- ``init_repo_rules`` points that same merge at the repo's own CLAUDE.md /
  AGENTS.md, touching only files that already exist.
"""

from __future__ import annotations

import pytest

from gingugu.bootstrap import init_claude_code, main
from gingugu.bootstrap.global_rules import (
    BEGIN_MARKER,
    END_MARKER,
    adopt_unmanaged_protocol,
    init_repo_rules,
)

PROTOCOL = "## Memory Protocol\n\nLoad crow first.\n"

# Mirrors the real shape found in this repo's own AGENTS.md and global
# CLAUDE.md: an H1 title, unrelated sections, then the memory protocol under
# its own H2 with H3 subsections — one of which (Daily protocol) mentions a
# tool name in its BODY without saying "memory protocol" in its own title.
# That decoy is the regression case: an early version of the detector matched
# on section body text and picked this narrower subsection over the true
# enclosing H2, because the subsection's span was smaller and therefore
# looked more "specific".
HAND_WRITTEN = """# My Rules

## Identity

You are a pirate robot.

## Memory Protocol (Gingugu)

Two layers: crow and per-project.

### Session start

Load crow first, always.

### Daily protocol

Run `memory_recall` before asking.

## Git Workflow

- Run `git status` before committing.
"""


# --- heading detection + adopt --------------------------------------------


def test_finds_and_wraps_the_whole_headed_section_including_subsections():
    wrapped = adopt_unmanaged_protocol(HAND_WRITTEN)

    assert wrapped is not None
    assert BEGIN_MARKER in wrapped and END_MARKER in wrapped
    begin = wrapped.index(BEGIN_MARKER)
    end = wrapped.index(END_MARKER)
    section = wrapped[begin:end]
    assert "Memory Protocol (Gingugu)" in section
    assert "Session start" in section
    assert "Daily protocol" in section
    # Neighbouring sections are untouched and outside the markers.
    assert "You are a pirate robot." in wrapped[:begin]
    assert "Run `git status`" in wrapped[end:]


def test_wrapping_does_not_touch_bytes_outside_the_section():
    wrapped = adopt_unmanaged_protocol(HAND_WRITTEN)
    assert wrapped is not None
    assert wrapped.startswith("# My Rules\n\n## Identity\n\nYou are a pirate robot.\n")
    assert wrapped.rstrip("\n").endswith("- Run `git status` before committing.")


def test_no_heading_at_all_returns_none():
    assert adopt_unmanaged_protocol("just some memory protocol prose, no headings\n") is None


def test_no_matching_heading_returns_none():
    text = "# Title\n\n## Unrelated Section\n\nNothing about memory here.\n"
    assert adopt_unmanaged_protocol(text) is None


def test_a_subsection_body_mentioning_a_tool_name_does_not_outrank_its_parent():
    """Regression: title-only matching, not body matching.

    ``### Daily protocol`` never says "memory protocol" in its own title, but
    its body names `memory_recall`. Matching on body text made this narrower
    subsection look more "specific" than the true enclosing
    ``## Memory Protocol (Gingugu)`` heading and win over it — which silently
    orphaned everything from ``### Session start`` onward outside the markers
    the first time this ran for real. The wrap must start at the H2, not the H3.
    """
    wrapped = adopt_unmanaged_protocol(HAND_WRITTEN)
    assert wrapped is not None
    begin = wrapped.index(BEGIN_MARKER)
    # The H2 heading itself is inside the wrap, not left dangling before it.
    assert "## Memory Protocol (Gingugu)" in wrapped[begin:]
    assert "## Memory Protocol (Gingugu)" not in wrapped[:begin]


def test_wrap_leaves_the_bytes_right_after_the_section_untouched():
    """`adopt_unmanaged_protocol` in isolation preserves whatever followed the
    section verbatim. (The end-to-end `--adopt` path immediately re-runs
    `merge_block` on this result, whose refresh logic collapses any blank
    line there to a single newline — existing, shipped behavior, not
    something this function needs to compensate for.)
    """
    wrapped = adopt_unmanaged_protocol(HAND_WRITTEN)
    assert wrapped is not None
    assert f"{END_MARKER}\n## Git Workflow" in wrapped


def test_wrap_at_end_of_file_has_no_trailing_content():
    text = "# Title\n\n## Memory Protocol\n\nBody.\n"
    wrapped = adopt_unmanaged_protocol(text)
    assert wrapped is not None
    assert wrapped.endswith(f"{END_MARKER}\n")


# --- init_repo_rules --------------------------------------------------------


def test_skips_repo_files_that_do_not_exist(tmp_path):
    out = "\n".join(init_repo_rules(tmp_path, dry_run=False))
    assert "none present" in out
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_appends_to_an_existing_repo_claude_md_with_no_protocol(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Repo Conventions\n\nUse black.\n")

    out = "\n".join(init_repo_rules(tmp_path, dry_run=False))

    assert "appended" in out
    text = claude_md.read_text()
    assert text.startswith("# Repo Conventions\n\nUse black.\n")
    assert BEGIN_MARKER in text


def test_never_creates_a_missing_agents_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Repo Conventions\n")

    init_repo_rules(tmp_path, dry_run=False)

    assert not (tmp_path / "AGENTS.md").exists()


def test_conflict_on_repo_file_writes_nothing_without_adopt(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(HAND_WRITTEN)

    out = "\n".join(init_repo_rules(tmp_path, dry_run=False))

    assert agents_md.read_text() == HAND_WRITTEN
    assert "WARNING" in out
    assert "--adopt" in out


def test_adopt_wraps_and_refreshes_a_repo_file_in_one_run(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(HAND_WRITTEN)

    out = "\n".join(init_repo_rules(tmp_path, dry_run=False, adopt=True))

    assert "adopted" in out
    text = agents_md.read_text()
    assert BEGIN_MARKER in text and END_MARKER in text
    # The rendered template replaced the hand-written wording, not the reverse.
    assert "Two layers: crow and per-project." not in text
    assert "Gingugu is your long-term brain" in text
    # Untouched neighbours survive.
    assert "You are a pirate robot." in text
    assert "Run `git status`" in text
    # Backup holds the true pre-adopt original.
    backup = agents_md.with_suffix(".md.bak")
    assert backup.read_text() == HAND_WRITTEN


def test_adopt_with_no_matching_heading_falls_back_to_conflict_message(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    original = "just some memory protocol prose, no headings\n"
    claude_md.write_text(original)

    out = "\n".join(init_repo_rules(tmp_path, dry_run=False, adopt=True))

    assert claude_md.read_text() == original
    assert "no markdown heading to wrap" in out


def test_dry_run_adopt_writes_nothing(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(HAND_WRITTEN)

    init_repo_rules(tmp_path, dry_run=True, adopt=True)

    assert agents_md.read_text() == HAND_WRITTEN
    assert not agents_md.with_suffix(".md.bak").exists()


# --- CLI wiring --------------------------------------------------------------


def test_init_claude_code_manages_existing_repo_rules_files(tmp_path):
    repo = tmp_path
    (repo / "CLAUDE.md").write_text("# Conventions\n\nUse black.\n")

    init_claude_code(repo, force=False, dry_run=False)

    assert BEGIN_MARKER in (repo / "CLAUDE.md").read_text()


@pytest.mark.parametrize("client", ["windsurf", "cursor", "cline"])
def test_adopt_rejected_for_non_claude_code_clients(tmp_path, client):
    assert main(["--path", str(tmp_path), "--client", client, "--adopt"]) == 1
    assert not (tmp_path / "CLAUDE.md").exists()


def test_adopt_flag_reaches_repo_files_end_to_end(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(
        "gingugu.bootstrap.global_rules.global_claude_md",
        lambda: home / ".claude" / "CLAUDE.md",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(HAND_WRITTEN)

    assert main(["--path", str(repo), "--adopt"]) == 0

    text = (repo / "AGENTS.md").read_text()
    assert BEGIN_MARKER in text
    assert "Two layers: crow and per-project." not in text
