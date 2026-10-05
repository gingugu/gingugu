"""``gingugu harness`` - the Claude Code harness, installed on top of ``gingugu init``.

Plain ``init`` stays gingugu-only. ``harness`` runs ``init`` and then adds the
rest of the kit: a safety guard and event logging, three fenced minions, the
``creating-pr`` skill, the ``.ai/`` knowledge base, and a managed block in the
repo's ``CLAUDE.md``. Nothing it installs calls out to the network.

Ownership rules, in one place:

- hooks, agents and skill files are *managed*: written through ``write_file``
  (skip if present, ``--force`` overwrites, a ``.bak`` whenever bytes change);
- ``.ai/`` files are the user's content once created: written only when missing,
  never overwritten, never backed up;
- ``CLAUDE.md`` is the user's file: only the text between our markers is ours.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..prompt_hook import _PERSONA_RE as NAMESPACE_RE
from . import init_claude_code, theme
from ._files import read_template, safe_read, write_file
from .harness_settings import wire_harness_settings
from .settings import is_user_level

HARNESS_BEGIN = "<!-- BEGIN GINGUGU HARNESS -->"
HARNESS_END = "<!-- END GINGUGU HARNESS -->"

_T = "harness/"
_MANAGED = [
    ("hooks/log_event.py", ".claude/hooks/log_event.py"),
    ("hooks/pre_tool_use.py", ".claude/hooks/pre_tool_use.py"),
    ("hooks/pre_compact.py", ".claude/hooks/pre_compact.py"),
    ("agents/repo-scout.md", ".claude/agents/repo-scout.md"),
    ("agents/security-reviewer.md", ".claude/agents/security-reviewer.md"),
    ("agents/ai-docs-auditor.md", ".claude/agents/ai-docs-auditor.md"),
    ("skills/creating-pr/SKILL.md", ".claude/skills/creating-pr/SKILL.md"),
    (
        "skills/creating-pr/ai-assessment-checklist.md",
        ".claude/skills/creating-pr/ai-assessment-checklist.md",
    ),
    ("skills/creating-pr/stacking-prs.md", ".claude/skills/creating-pr/stacking-prs.md"),
]
_AI_FILES = (
    "memory.md",
    "plans/status.md",
    "specs/01-architecture.md",
    "specs/product-spec.md",
    "specs/dataflow.md",
    "standards/01-code-and-testing.md",
)


def minion_grant(namespace: str) -> str:
    """The minions' grant: read ``crow`` and this repo, write only ``minions``.

    A repo named ``crow`` is listed once - a namespace granted twice is refused.
    """
    reads = ["crow"] if namespace == "crow" else ["crow", namespace]
    return ",".join([*(f"{name}=read" for name in reads), "minions=write"])


def _install_managed(
    target: Path, namespace: str, *, force: bool, dry_run: bool, results: list[str]
) -> None:
    grant = minion_grant(namespace)
    for template, dest in _MANAGED:
        content = read_template(f"{_T}{template}.tmpl")
        content = content.replace("{{grant}}", grant).replace("{{namespace}}", namespace)
        write_file(target / dest, content, force=force, dry_run=dry_run, results=results)


def _scaffold_ai(target: Path, *, dry_run: bool, results: list[str]) -> None:
    """Create each ``.ai/`` file only if missing - it is the user's content."""
    for rel in _AI_FILES:
        path = target / ".ai" / rel
        if path.exists():
            results.append(f"  skip   {path}  (exists; never overwritten)")
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(read_template(f"{_T}ai/{rel}.tmpl"))
        results.append(f"  {'would write' if dry_run else 'write':<9} {path}")


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
    if not path.exists():  # dry-run: init could not have created it either
        results.append(f"  would append harness block to {path}")
        return
    existing = safe_read(path)
    merged = merge_harness_block(existing, read_template(f"{_T}claude_md_block.md.tmpl"))
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gingugu harness",
        description="Run `gingugu init`, then install the Claude Code harness into a project.",
    )
    parser.add_argument("--path", default=".", help="Target repo directory (default: current dir)")
    parser.add_argument("--force", action="store_true", help="Overwrite managed files that exist")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would happen, write nothing"
    )
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser().resolve()
    if not target.is_dir():
        print(f"error: target path is not a directory: {target}")
        return 1
    if is_user_level(target):
        print("error: `gingugu harness` installs hooks and agents, so run it inside a project.")
        return 1
    namespace = target.name
    if not NAMESPACE_RE.fullmatch(namespace):
        print(
            f"error: the directory name {namespace!r} cannot be a memory namespace "
            "(letters, digits, '.', '_', '-'; must start with a letter or digit; max 64)."
        )
        return 1
    if namespace == "minions":
        print("error: 'minions' is the minions' scratch namespace; it cannot be a repo's.")
        return 1

    claude_md = target / "CLAUDE.md"
    original = safe_read(claude_md) if claude_md.exists() else None
    if not claude_md.exists() and not args.dry_run:
        # Created before init so its protocol block merges in this same run.
        claude_md.write_text(f"# {namespace}\n")
    settings_existed = (target / ".claude" / "settings.json").exists()

    results = init_claude_code(target, force=args.force, dry_run=args.dry_run)
    project_settings = str(target / ".claude" / "settings.json")
    init_backed_up = any(
        project_settings in line and "settings.json.bak" in line for line in results
    )

    results.extend(["", "Claude Code harness:"])
    _install_managed(target, namespace, force=args.force, dry_run=args.dry_run, results=results)
    _scaffold_ai(target, dry_run=args.dry_run, results=results)
    _apply_claude_md(target, original, dry_run=args.dry_run, results=results)
    lines, _ = wire_harness_settings(
        target,
        dry_run=args.dry_run,
        user_original_kept=init_backed_up or not settings_existed,
    )
    results.extend(lines)

    print(theme.render(results, dry_run=args.dry_run))
    return 0
