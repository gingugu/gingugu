"""The ``.gitignore`` rules ``gingugu init`` keeps in a repo, added non-destructively."""

from __future__ import annotations

from pathlib import Path

# Runtime artifacts the installed hooks (and Claude Code itself) generate. These
# must be git-ignored so a session transcript or local override never lands in
# the repo — especially on a public one. The ``.bak`` files are init's own:
# the copy of a user's settings or hook it saves before replacing one.
GITIGNORE_ENTRIES = [
    "logs/",
    ".claude/data/",
    ".claude/settings.local.json",
    ".claude/hooks/**/__pycache__/",
    ".claude/**/*.bak",
    "CLAUDE.md.bak",
    "AGENTS.md.bak",
]

GITIGNORE_HEADER = "# Claude Code / Gingugu artifacts (added by `gingugu init`)"


def merge_gitignore(existing: str, missing: list[str]) -> str:
    """``existing`` with ``missing`` added to init's block, or a new block appended.

    An earlier run's block - the header and the rules of ours right under it -
    takes the new rules at its end, so a rerun never opens a second block under
    the same header. A user line there (a ``!negation``) stays after ours, since
    in a gitignore the last matching rule wins. Every line is kept as it was.
    """
    lines = existing.splitlines(keepends=True)
    heads = [i for i, line in enumerate(lines) if line.strip() == GITIGNORE_HEADER]
    if not heads:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        prefix = "\n" if existing.strip() else ""
        return existing + sep + prefix + GITIGNORE_HEADER + "\n" + "\n".join(missing) + "\n"
    end = heads[0] + 1
    while end < len(lines) and lines[end].strip() in GITIGNORE_ENTRIES:
        end += 1
    if not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    lines[end:end] = [f"{entry}\n" for entry in missing]
    return "".join(lines)


def ensure_gitignore(target: Path, *, dry_run: bool, results: list[str]) -> None:
    """Add any missing Claude Code / Gingugu ignore rules, non-destructively."""
    path = target / ".gitignore"
    existing = path.read_text() if path.exists() else ""
    present = {line.strip() for line in existing.splitlines()}
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in present]
    if not missing:
        results.append(f"  .gitignore already covers Claude Code artifacts {path}")
        return
    if not dry_run:
        path.write_text(merge_gitignore(existing, missing))
    verb = "would update" if dry_run else "updated"
    results.append(f"  {verb} {path}  (+{len(missing)} ignore rule(s))")
