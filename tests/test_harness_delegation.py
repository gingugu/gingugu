"""`gingugu harness` installs the subagent-delegation rules at user level.

Two pieces: a managed block in ``~/.claude/CLAUDE.md`` (the rules, loaded every
request, kept short) and ``~/.claude/docs/delegation.md`` (the rationale, read on
demand). Same ownership rules as everything else we write into a hand-authored
file: only the text between our markers is ours, a hand-written section is
never replaced without ``--adopt``, and a rewrite is backed up first.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gingugu.bootstrap._files import read_template
from gingugu.bootstrap.harness import main
from gingugu.bootstrap.harness_global import (
    DELEGATION_BEGIN,
    DELEGATION_END,
    install_delegation,
)

HAND_WRITTEN = """# My Rules

## Identity

You are a pirate robot.

## Subagent Delegation (Minions)

My own old minion rules.

### Model tiers

Haiku for greps.

## Git Workflow

- Run `git status`.
"""


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    return claude_md, tmp_path / ".claude" / "docs" / "delegation.md"


def _run(tmp_path, **kw) -> str:
    claude_md, docs = _paths(tmp_path)
    kw.setdefault("dry_run", False)
    return "\n".join(install_delegation(claude_md=claude_md, docs=docs, **kw))


def test_appends_block_and_writes_docs_file(tmp_path):
    claude_md, docs = _paths(tmp_path)
    claude_md.write_text("# Mine\n\nBe terse.\n")

    out = _run(tmp_path)

    text = claude_md.read_text()
    assert text.startswith("# Mine\n\nBe terse.\n")
    assert DELEGATION_BEGIN in text and DELEGATION_END in text
    assert "~/.claude/docs/delegation.md" in text
    assert docs.read_text() == read_template("harness/docs/delegation.md.tmpl")
    assert "appended" in out


def test_rerun_is_a_no_op(tmp_path):
    claude_md, docs = _paths(tmp_path)
    claude_md.write_text("# Mine\n")
    _run(tmp_path)
    before = (claude_md.read_text(), docs.read_text())

    out = _run(tmp_path)

    assert (claude_md.read_text(), docs.read_text()) == before
    assert not claude_md.with_suffix(".md.bak").exists()
    assert "no change" in out


def test_stale_block_is_refreshed_with_backup(tmp_path):
    claude_md, _ = _paths(tmp_path)
    stale = f"# Mine\n\n{DELEGATION_BEGIN}\nold rules\n{DELEGATION_END}\n\n## After\n"
    claude_md.write_text(stale)

    _run(tmp_path)

    text = claude_md.read_text()
    assert "old rules" not in text and text.endswith("## After\n")
    assert claude_md.with_suffix(".md.bak").read_text() == stale


def test_hand_written_section_is_left_alone_without_adopt(tmp_path):
    claude_md, _ = _paths(tmp_path)
    claude_md.write_text(HAND_WRITTEN)

    out = _run(tmp_path)

    assert claude_md.read_text() == HAND_WRITTEN
    assert "WARNING" in out and "--adopt" in out


def test_adopt_replaces_the_hand_written_section_in_place(tmp_path):
    claude_md, _ = _paths(tmp_path)
    claude_md.write_text(HAND_WRITTEN)

    out = _run(tmp_path, adopt=True)

    text = claude_md.read_text()
    assert "My own old minion rules." not in text and "Haiku for greps." not in text
    assert text.index("You are a pirate robot.") < text.index(DELEGATION_BEGIN)
    assert text.index(DELEGATION_END) < text.index("## Git Workflow")
    assert text.count("## Subagent Delegation") == 1
    assert claude_md.with_suffix(".md.bak").read_text() == HAND_WRITTEN
    assert "adopted" in out


def test_adopt_stops_before_a_managed_block_that_follows(tmp_path):
    """The real layout: own delegation section, then the memory-protocol block,
    whose BEGIN comment sits above its first heading. Adopting must not eat it."""
    claude_md, _ = _paths(tmp_path)
    protocol = (
        "<!-- BEGIN GINGUGU MEMORY PROTOCOL -->\n<!-- note -->\n\n## Memory Protocol\n\n"
        "Load crow.\n\n<!-- END GINGUGU MEMORY PROTOCOL -->\n"
    )
    claude_md.write_text(f"# Mine\n\n## Subagent Delegation (Minions)\n\nold rules\n\n{protocol}")

    _run(tmp_path, adopt=True)

    text = claude_md.read_text()
    assert "old rules" not in text
    assert text.endswith(protocol)
    assert text.index(DELEGATION_END) < text.index("<!-- BEGIN GINGUGU MEMORY PROTOCOL -->")


def test_backup_is_the_pre_run_original_not_inits_intermediate(tmp_path):
    """init may already have rewritten the file (and its .bak) earlier in the run."""
    claude_md, _ = _paths(tmp_path)
    claude_md.write_text(f"intermediate\n\n{DELEGATION_BEGIN}\nold\n{DELEGATION_END}\n")

    _run(tmp_path, original="the true original\n")

    assert claude_md.with_suffix(".md.bak").read_text() == "the true original\n"


def test_edited_docs_file_is_kept(tmp_path):
    claude_md, docs = _paths(tmp_path)
    claude_md.write_text("# Mine\n")
    docs.parent.mkdir(parents=True)
    docs.write_text("my own notes\n")

    _run(tmp_path)

    assert docs.read_text() == "my own notes\n"


def test_adopting_the_section_also_takes_over_an_edited_docs_file_with_backup(tmp_path):
    claude_md, docs = _paths(tmp_path)
    claude_md.write_text(HAND_WRITTEN)
    docs.parent.mkdir(parents=True)
    docs.write_text("my own notes\n")

    _run(tmp_path, adopt=True)

    assert docs.read_text() == read_template("harness/docs/delegation.md.tmpl")
    assert (docs.parent / "delegation.md.bak").read_text() == "my own notes\n"


def test_dry_run_writes_nothing(tmp_path):
    claude_md, docs = _paths(tmp_path)
    claude_md.write_text(HAND_WRITTEN)

    out = _run(tmp_path, dry_run=True, adopt=True)

    assert claude_md.read_text() == HAND_WRITTEN
    assert not docs.exists()
    assert not claude_md.with_suffix(".md.bak").exists()
    assert "would" in out


def test_shipped_text_names_no_one():
    """Public repo: the rules address 'the user', never a person."""
    for name in ("harness/global_delegation_block.md.tmpl", "harness/docs/delegation.md.tmpl"):
        text = read_template(name)
        assert "Boomtastic" not in text and "BeepBoop" not in text.replace("Beepboop", "")


# --- wiring ----------------------------------------------------------------------


def test_harness_run_installs_it_in_the_sandboxed_global_file(tmp_path, sandboxed_global_rules):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert main(["--path", str(repo)]) == 0

    global_md = sandboxed_global_rules
    assert DELEGATION_BEGIN in global_md.read_text()
    assert (global_md.parent / "docs" / "delegation.md").exists()


def test_harness_adopt_flag_reaches_the_global_file(tmp_path, sandboxed_global_rules):
    global_md = sandboxed_global_rules
    global_md.parent.mkdir(parents=True, exist_ok=True)
    global_md.write_text(HAND_WRITTEN)
    repo = tmp_path / "repo"
    repo.mkdir()

    assert main(["--path", str(repo), "--adopt"]) == 0

    assert "My own old minion rules." not in global_md.read_text()


# --- security review findings (2026-11-17) -----------------------------------------


def test_crlf_file_keeps_its_bytes_and_the_backup_is_exact(tmp_path):
    claude_md, _ = _paths(tmp_path)
    original = b"# Mine\r\n\r\nBe terse.\r\n"
    claude_md.write_bytes(original)
    _run(tmp_path)  # append: every pre-existing byte stays put
    after = claude_md.read_bytes()
    assert after.startswith(original) and b"\n" not in after.replace(b"\r\n", b"")

    stale = after.replace(b"Subagent Delegation", b"Old Delegation")
    claude_md.write_bytes(stale)
    _run(tmp_path)  # refresh: the .bak is the exact bytes
    assert claude_md.with_suffix(".md.bak").read_bytes() == stale


def test_crlf_file_rerun_is_a_byte_identical_no_op(tmp_path):
    claude_md, _ = _paths(tmp_path)
    claude_md.write_bytes(b"# Mine\r\n\r\nBe terse.\r\n")
    _run(tmp_path)
    once = claude_md.read_bytes()

    out = _run(tmp_path)

    assert claude_md.read_bytes() == once
    assert b"\n" not in once.replace(b"\r\n", b"")  # no bare LF anywhere
    assert not claude_md.with_suffix(".md.bak").exists()
    assert "no change" in out


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX perms, non-root")
def test_unreadable_file_stops_the_run_and_is_not_truncated(tmp_path):
    claude_md, _ = _paths(tmp_path)
    claude_md.write_text("# Mine\n\nprecious\n")
    claude_md.chmod(0o200)
    try:
        with pytest.raises(PermissionError):
            _run(tmp_path)
    finally:
        claude_md.chmod(0o600)
    assert claude_md.read_text() == "# Mine\n\nprecious\n"


def test_adopt_without_an_own_section_keeps_an_edited_docs_file(tmp_path):
    claude_md, docs = _paths(tmp_path)
    claude_md.write_text("# Mine\n")  # nothing to adopt here
    docs.parent.mkdir(parents=True)
    docs.write_text("my own notes\n")

    _run(tmp_path, adopt=True)

    assert docs.read_text() == "my own notes\n"


def test_nested_or_fenced_delegation_headings_are_not_the_section(tmp_path):
    claude_md, _ = _paths(tmp_path)
    text = (
        "# Mine\n\n## Reviews\n\n### Delegation of reviews\n\nkeep me\n\n"
        "## Examples\n\n```md\n## Delegation\nexample only\n```\n"
    )
    claude_md.write_text(text)

    _run(tmp_path, adopt=True)

    out = claude_md.read_text()
    assert out.startswith(text) and DELEGATION_BEGIN in out  # appended, nothing eaten


def test_init_adopt_of_a_last_memory_section_keeps_the_delegation_marker():
    """A hand-written memory section last in the file, then our appended block:
    init's --adopt must stop before the block's BEGIN comment."""
    from gingugu.bootstrap.global_rules import END_MARKER, adopt_unmanaged_protocol

    text = (
        f"# Mine\n\n## Memory Protocol\n\nRun `memory_recall`.\n\n"
        f"{DELEGATION_BEGIN}\n<!-- note -->\n\n## Subagent Delegation\n\nrules\n\n"
        f"{DELEGATION_END}\n"
    )
    wrapped = adopt_unmanaged_protocol(text)

    assert wrapped is not None
    assert wrapped.index(END_MARKER) < wrapped.index(DELEGATION_BEGIN)
