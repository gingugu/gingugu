"""The managed harness block in a repo's ``CLAUDE.md`` (split out of ``harness``)."""

from __future__ import annotations

from pathlib import Path

from ._files import read_template, safe_read

HARNESS_BEGIN = "<!-- BEGIN GINGUGU HARNESS -->"
HARNESS_END = "<!-- END GINGUGU HARNESS -->"
_T = "harness/"


def merge_harness_block(existing: str, body: str) -> str:
    """Return ``existing`` with the harness block replaced, or appended if absent.

    Everything outside the markers is preserved byte for byte.
    """
    block = f"{HARNESS_BEGIN}\n{body.strip()}\n{HARNESS_END}"
    start = existing.find(HARNESS_BEGIN)
    end = existing.find(HARNESS_END)
    if start != -1 and end > start:
        return existing[:start] + block + existing[end + len(HARNESS_END) :]
    sep = "" if not existing or existing.endswith("\n") else "\n"
    return f"{existing}{sep}\n{block}\n" if existing.strip() else f"{block}\n"


def _block_of(text: str) -> str | None:
    """The marked block in ``text``, markers included, or None when absent."""
    start, end = text.find(HARNESS_BEGIN), text.find(HARNESS_END)
    return text[start : end + len(HARNESS_END)] if start != -1 and end > start else None


def _apply_claude_md(
    target: Path, original: str | None, *, dry_run: bool, results: list[str]
) -> None:
    """Merge the harness block. ``original`` is the file before this run, if any.

    The backup is that pre-run text, not what init left behind: init may have
    refreshed its own block (and written CLAUDE.md.bak) moments earlier.
    """
    path = target / "CLAUDE.md"
    if path.is_symlink() and not path.exists():  # dangling; init skipped it too
        return
    if not path.exists():  # dry-run: init could not have created it either
        results.append(f"  would append harness block to {path}")
        return
    existing = safe_read(path)
    body = read_template(f"{_T}claude_md_block.md.tmpl")
    heading = body.lstrip().splitlines()[0].strip()
    if _block_of(existing) is None and heading in {ln.strip() for ln in existing.splitlines()}:
        results.append(f"  skip   {path}  (has its own '{heading}'; harness block not added)")
        return
    merged = merge_harness_block(existing, body)
    if merged == existing:
        results.append(f"  harness block already current (no change) {path}")
        return
    old_block = _block_of(existing)
    # Back up only when the block's own text changes, not a restored blank line.
    backup = old_block is not None and old_block != _block_of(merged)
    if not dry_run:
        if backup:
            (target / "CLAUDE.md.bak").write_text(existing if original is None else original)
        path.write_text(merged)
    if old_block is None:
        verb = "would append" if dry_run else "appended"
    else:
        verb = "would refresh" if dry_run else "refreshed"
    note = " (backed up previous to CLAUDE.md.bak)" if backup and not dry_run else ""
    results.append(f"  {verb} harness block in {path}{note}")
