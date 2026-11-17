"""The managed memory protocol in a repo's own CLAUDE.md / AGENTS.md.

Same no-``--force`` merge as the user-level file (see ``global_rules``), with one
difference: Claude Code loads ``~/.claude/CLAUDE.md`` AND the repo ``CLAUDE.md``
into every request. When the user-level file already carries the managed block
(``global_covers``), a second copy in the repo ``CLAUDE.md`` is the same ~6k
characters paid twice per request, so the repo file gets none - and a copy left
by an earlier init is removed, backed up first. ``AGENTS.md`` keeps its block:
other clients read it, and Claude Code does not auto-load it.
"""

from __future__ import annotations

from pathlib import Path

from ._files import read_template, safe_read
from .global_rules import BEGIN_MARKER, END_MARKER, _apply_protocol

REPO_RULES_FILES = ("CLAUDE.md", "AGENTS.md")
_GLOBAL_NOTE = "protocol already loads from ~/.claude/CLAUDE.md"


def strip_block(existing: str) -> str | None:
    """``existing`` without its managed block, or None when it has none.

    Joins the neighbours with one blank line, so a block appended by
    ``merge_block`` comes out as if it had never been added.
    """
    start, end = existing.find(BEGIN_MARKER), existing.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        return None
    head = existing[:start].rstrip("\n")
    tail = existing[end + len(END_MARKER) :].lstrip("\n")
    if not head:
        return tail
    return f"{head}\n\n{tail}" if tail else f"{head}\n"


def _dedupe_claude_md(path: Path, *, dry_run: bool, new: bool, title: str) -> str:
    """Keep the repo CLAUDE.md free of the block the user-level file carries."""
    if new:
        if not dry_run:
            path.write_text(f"# {title}\n")
        verb = "would create" if dry_run else "created"
        return f"  {verb:<13} {path}  (no protocol block: {_GLOBAL_NOTE})"
    existing = safe_read(path)
    stripped = strip_block(existing)
    if stripped is None:
        return f"  skip          {path}  ({_GLOBAL_NOTE})"
    if not dry_run:
        path.with_suffix(path.suffix + ".bak").write_text(existing)
        path.write_text(stripped)
    verb = "would remove" if dry_run else "removed"
    return f"  {verb:<13} {path}  (duplicate protocol block; {_GLOBAL_NOTE})"


def init_repo_rules(
    target: Path,
    *,
    dry_run: bool,
    adopt: bool = False,
    create: bool = False,
    global_covers: bool = False,
) -> list[str]:
    """Merge the managed protocol block into the repo's own CLAUDE.md / AGENTS.md.

    Only touches files that already exist — creating an AGENTS.md where none
    exists would be presumptuous, and the SessionStart hook already carries the
    protocol into every session regardless of whether a rules file mentions it.
    ``create`` (``gingugu harness``, which needs a CLAUDE.md) is the exception. No
    ``--force``: like the global file, it is hand-authored and loaded every session.
    ``global_covers``: see the module docstring.
    """
    protocol = read_template("rules_protocol.md.tmpl")
    results: list[str] = ["Repo rules files (CLAUDE.md / AGENTS.md):"]
    found = False
    for name in REPO_RULES_FILES:
        rules_path = target / name
        new = create and name == "CLAUDE.md" and not rules_path.exists()
        if not rules_path.exists() and not new:
            continue
        found = True
        if new and rules_path.is_symlink():  # dangling: a create would land at its target
            results.append(f"  skip          {rules_path}  (a symlink; never written through)")
            continue
        if global_covers and name == "CLAUDE.md":
            results.append(
                _dedupe_claude_md(rules_path, dry_run=dry_run, new=new, title=target.name)
            )
            continue
        existing = f"# {target.name}\n" if new else safe_read(rules_path)
        lines, _ = _apply_protocol(
            rules_path, existing, protocol, dry_run=dry_run, adopt=adopt, new=new
        )
        results.extend(lines)
    if not found:
        results.append(f"  none present ({', '.join(REPO_RULES_FILES)}); nothing to do")
    return results
