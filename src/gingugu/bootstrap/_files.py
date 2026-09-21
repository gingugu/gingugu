"""Shared file/template helpers for the bootstrap package.

Extracted so ``global_rules`` can use them without importing the package
``__init__`` that imports it back — a cycle that otherwise only works by
accident of import ordering.

The write primitives live here rather than in ``__init__`` because they are the
one concept every install path shares: *never destroy the user's bytes without a
copy*. Keeping them together means a new write path cannot accidentally get a
weaker guarantee than the existing ones — the guarantee is in the function, not
in the caller's care.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

# A distinctive marker every file we ship carries. Its absence in a file we are
# about to overwrite means that file is NOT ours, so back it up first.
#
# This was the bare word "gingugu", which is useless as a signature: any hook
# that merely mentions the tool matches, and every gingugu-aware hook does — the
# MCP tool names are `mcp__gingugu__*`. A real, heavily-customized local hook was
# therefore classified as ours and overwritten by `--force` with NO backup, and
# only a clean git tree saved it. The marker has to be something only our
# templates would ever contain.
TEMPLATE_SIGNATURE = "gingugu-init:managed-file"


def read_template(name: str) -> str:
    """Read a packaged template from ``gingugu/bootstrap/templates``."""
    return (files("gingugu.bootstrap") / "templates" / name).read_text()


def safe_read(path: Path) -> str:
    """Read ``path``, returning "" instead of raising when it cannot be read."""
    try:
        return path.read_text()
    except OSError:
        return ""


def write_file(
    path: Path,
    content: str,
    *,
    force: bool,
    dry_run: bool,
    results: list[str],
    skip_hint: str = "",
) -> None:
    if path.exists() and not force:
        results.append(f"  skip   {path}  (exists; use --force to overwrite){skip_hint}")
        return

    existing = safe_read(path) if path.exists() else None
    # Back up whenever `--force` would change what is on disk - NOT only when the
    # file looks foreign.
    #
    # The backup used to be conditioned on TEMPLATE_SIGNATURE being ABSENT, which
    # meant the net disappeared the moment it did its job: the first `--force`
    # wrote a .bak and stamped the marker, and every `--force` after that saw its
    # own marker and destroyed the user's edits silently. A file being ours says
    # nothing about whether the user has since customized it.
    changed = existing is not None and existing != content
    foreign = (
        existing is not None
        and TEMPLATE_SIGNATURE in content
        and TEMPLATE_SIGNATURE not in existing
    )
    if changed and not dry_run:
        (path.parent / f"{path.name}.bak").write_text(existing or "")

    verb = "would write" if dry_run else ("overwrite" if path.exists() else "write")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    results.append(f"  {verb:<9} {path}")

    if foreign:
        results.append(
            f"  WARNING: {path.name} was not written by this version of `gingugu "
            f"init` — it may be your own or another tool's. Backed up to "
            f"{path.name}.bak. If your settings.json invokes it with flags the "
            f"replacement does not declare, that command needs updating too."
        )
    elif changed:
        would = "would back up" if dry_run else "backed up"
        results.append(f"  {would} your version to {path.name}.bak")


def retire_file(path: Path, *, dry_run: bool, results: list[str], superseded_by: str) -> None:
    """Remove a file this tool shipped that a newer location has replaced.

    Retirement is not the same as overwriting, and it earns a stricter test:
    **only a file carrying ``TEMPLATE_SIGNATURE`` is ever removed.** A file at
    the old path without our marker is the user's own — leaving a duplicate
    behind is a nuisance, deleting someone's work is not recoverable, and the
    nuisance is the correct trade.

    A ``.bak`` is written unconditionally before the removal, even though the
    file is ours. The last time a decision here reasoned from authorship rather
    than from content, `--force` destroyed customized hooks silently for three
    releases. Ownership says nothing about whether the user edited it since.

    This deliberately does not take ``force``. The duplicate it resolves is a
    correctness problem — two definitions of the same command answering to one
    name — and the ``.bak`` means nothing is lost by fixing it on a plain run.
    """
    if not path.exists():
        return

    existing = safe_read(path)
    if TEMPLATE_SIGNATURE not in existing:
        results.append(
            f"  kept   {path}  (superseded by {superseded_by}, but this copy is "
            f"not ours — remove it yourself if you no longer want it)"
        )
        return

    if not dry_run:
        (path.parent / f"{path.name}.bak").write_text(existing)
        path.unlink()

    verb = "would retire" if dry_run else "retired"
    results.append(f"  {verb} {path}  (superseded by {superseded_by}; saved {path.name}.bak)")
