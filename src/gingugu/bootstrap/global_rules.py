"""User-level rules file - the memory protocol in the global ``CLAUDE.md``.

Part of every ``gingugu init`` run; there is no flag for it, and deliberately so
(see ``init_claude_code``). A ``--global`` flag would imply the step is optional.

Why this is a separate path from ``init_rules_file``: a per-repo rules file is
written by ``gingugu init``, so a whole-file write gated on ``--force`` (plus a
``.bak``) is acceptable. The user-level file is the opposite. It is
hand-authored, it accumulates identity, working style, and workflow rules that
have nothing to do with memory, and it is loaded in **every** session — including
sessions in directories with no project protocol installed. Overwriting it would
destroy the user's configuration.

So this module does a **marked-section merge**: the protocol lives between two
sentinel comments and is replaced in place on re-run, leaving every surrounding
line byte-identical. That is what makes the file maintainable rather than
drift-prone — the reason this path exists at all is that the user-level file was
found still saying "build edges aggressively" long after the repo templates had
moved on, with no tooling able to correct it.

Nothing here honors ``--force``. That flag means "overwrite the repo files `init`
owns"; it must not also authorize appending a second set of memory rules to a
hand-authored file. Keeping them separate is not theoretical caution — a
``--force`` run aimed at a repo's hooks did exactly that before the two were
decoupled.
"""

from __future__ import annotations

from pathlib import Path

from ._files import read_template, safe_read

BEGIN_MARKER = "<!-- BEGIN GINGUGU MEMORY PROTOCOL -->"
END_MARKER = "<!-- END GINGUGU MEMORY PROTOCOL -->"

_MANAGED_NOTE = (
    "<!-- Managed by `gingugu init`. Edits between these markers are\n"
    "     replaced on re-run; put your own rules outside them. -->"
)

# Heuristic for "the user already hand-wrote a memory protocol here". Appending a
# managed block next to one would leave two sets of possibly-conflicting rules in
# a file that is loaded into every single session.
_UNMANAGED_HINTS = ("memory protocol", "memory_recall", "memory_store")


def global_claude_md() -> Path:
    """Claude Code's user-level instructions file.

    Verified from a live session: Claude Code loads this path as "the user's
    private global instructions for all projects". Kept as a function so tests
    can redirect it without monkeypatching ``Path.home`` globally.
    """
    return Path.home() / ".claude" / "CLAUDE.md"


def render_block(protocol: str) -> str:
    """The managed block, markers included, exactly as it lands on disk."""
    return f"{BEGIN_MARKER}\n{_MANAGED_NOTE}\n\n{protocol.strip()}\n\n{END_MARKER}\n"


def _has_unmanaged_protocol(existing: str) -> bool:
    lowered = existing.lower()
    return any(hint in lowered for hint in _UNMANAGED_HINTS)


def merge_block(existing: str, protocol: str) -> tuple[str | None, str]:
    """Fold the managed block into ``existing``.

    Returns ``(new_text, status)``. ``new_text`` is None when nothing should be
    written — either the block is already current, or an unmanaged protocol is
    present. ``status`` is one of ``current``, ``updated``, ``appended``, or
    ``conflict``, so the caller can phrase its own output and tests can assert
    the decision rather than the prose.

    Deliberately takes no ``force``. ``--force`` means "overwrite the repo files
    `init` owns", which is routine; appending a second set of memory rules to the
    file loaded in *every* session is not, and one flag must not authorize both.
    Learned the hard way: a `--force` run intended for a repo's hooks silently
    appended a duplicate protocol to a hand-authored user-level file. The only
    way to adopt management is to wrap the existing section in the markers by
    hand — a deliberate act, at a moment the user is thinking about this file.
    """
    block = render_block(protocol)

    start = existing.find(BEGIN_MARKER)
    end = existing.find(END_MARKER)
    if start != -1 and end != -1 and end > start:
        head = existing[:start]
        tail = existing[end + len(END_MARKER) :].lstrip("\n")
        rebuilt = f"{head}{block}{tail}"
        if rebuilt == existing:
            return None, "current"
        return rebuilt, "updated"

    # No managed block yet.
    if existing.strip() and _has_unmanaged_protocol(existing):
        return None, "conflict"

    if not existing.strip():
        return block, "appended"
    sep = "" if existing.endswith("\n") else "\n"
    return f"{existing}{sep}\n{block}", "appended"


def init_global_rules(*, dry_run: bool, path: Path | None = None) -> list[str]:
    """Install or refresh the managed protocol block in the user-level rules file.

    Never fails the run: this is an additive step inside the repo bootstrap, so a
    hand-written protocol in the user's file is a warning to act on, not a reason
    to report the repo bootstrap as failed. Takes no ``force`` — see
    ``merge_block``.
    """
    target = path or global_claude_md()
    results: list[str] = ["Global rules (loaded in every session):"]
    protocol = read_template("rules_protocol.md.tmpl")
    existing = safe_read(target) if target.exists() else ""

    new_text, status = merge_block(existing, protocol)

    if status == "current":
        results.append(f"  no change  {target}  (managed block already current)")
        return results

    if status == "conflict":
        results.append(f"  skip      {target}  (already has its own memory protocol)")
        results.append(
            "  WARNING: appending a second set of memory rules to a file loaded in "
            "every session would leave two possibly-conflicting protocols. Nothing "
            "was written, and no flag overrides this. To hand management to "
            f"gingugu, wrap your existing section in {BEGIN_MARKER} and "
            f"{END_MARKER} — future runs then refresh it in place."
        )
        return results

    assert new_text is not None  # status is 'updated' or 'appended'
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Only the refresh path rewrites bytes that already existed, so that is
        # the only path that needs a safety copy. Appending leaves every prior
        # byte in place and needs none.
        if status == "updated":
            target.with_suffix(target.suffix + ".bak").write_text(existing)
        target.write_text(new_text)

    if status == "updated":
        verb = "would refresh" if dry_run else "refreshed"
        detail = "managed block only; your own rules untouched"
    else:
        verb = "would append" if dry_run else "appended"
        detail = "managed block added below your existing rules" if existing.strip() else "new file"
    results.append(f"  {verb:<13} {target}  ({detail})")
    if status == "updated" and not dry_run:
        results.append(f"  backup        {target.name}.bak")

    results.append("")
    results.append(
        "Re-run `gingugu init` after upgrading gingugu to refresh the protocol in place."
    )
    return results
