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

import re
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


_HEADING_RE = re.compile(r"^(#{1,6}) (.*)$", re.MULTILINE)


def _find_unmanaged_section(existing: str) -> tuple[int, int] | None:
    """Locate the heading-bounded span of a hand-written protocol section.

    Returns ``(start, end)`` character offsets running from a markdown heading
    whose TITLE hits the ``_UNMANAGED_HINTS`` through the next heading at the
    same level or shallower (or end of file). ``None`` if no heading title in
    the file matches — the caller falls back to telling the user to wrap it by
    hand.

    Matches on the heading's own title line only, never its body. A real
    protocol section's title says so directly (e.g. "## Memory Protocol").
    Matching on body text instead is unreliable both ways: a real protocol
    section commonly has H3 subsections (Session start, Credentials, ...)
    whose OWN bodies happen to name a tool like `memory_recall` in passing —
    those subsections would then look like narrower, more "specific" matches
    than the true enclosing heading and win over it. Title-only matching
    treats such prose-only subsections as ordinary content, so the real H2
    title is the one and only match, and its span is used as found — no
    need to compare candidates against each other. Level-1 headings are
    skipped for the same reason a document's own title is never itself "the
    protocol section": it would trivially contain every match beneath it.
    """
    for m in _HEADING_RE.finditer(existing):
        level, title = len(m.group(1)), m.group(2)
        if level < 2 or not _has_unmanaged_protocol(title):
            continue
        start = m.start()
        end = len(existing)
        for other in _HEADING_RE.finditer(existing, m.end()):
            if len(other.group(1)) <= level:
                end = other.start()
                break
        return start, end
    return None


def adopt_unmanaged_protocol(existing: str) -> str | None:
    """Wrap a hand-written protocol section in the managed markers, verbatim.

    This does not replace the section's wording — it only adds the markers
    around whatever is already there. The caller re-runs ``merge_block`` on the
    result immediately after, which is what actually replaces the wrapped
    content with the rendered template (backed up first). Adopting means
    handing the section to gingugu going forward, not preserving the
    hand-written text.

    Whatever comes after the wrapped section ends up glued directly to
    ``END_MARKER`` with no separating blank line — not a bug introduced here,
    but the existing, shipped, tested behavior of ``merge_block``'s refresh
    path (its ``tail`` is always ``lstrip("\\n")``-ed), which this wrap
    immediately runs through too. Cosmetic only: an HTML comment directly
    followed by an ATX heading with no blank line still renders correctly.

    Returns ``None`` when no heading-bounded section matches, so the caller can
    fall back to the plain conflict message instead of guessing.
    """
    span = _find_unmanaged_section(existing)
    if span is None:
        return None
    start, end = span
    before, section, after = existing[:start], existing[start:end].rstrip("\n"), existing[end:]
    sep = "" if not before or before.endswith("\n") else "\n"
    return f"{before}{sep}{BEGIN_MARKER}\n{section}\n{END_MARKER}\n{after}"


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


def _apply_protocol(
    target: Path, existing: str, protocol: str, *, dry_run: bool, adopt: bool
) -> tuple[list[str], str]:
    """Merge the managed block into ``target``. Returns ``(result lines, status)``.

    Shared between the user-level file and the repo's own CLAUDE.md / AGENTS.md
    — both are hand-authored, both are loaded every session, so both get the
    same no-``--force`` merge and the same ``--adopt`` escape hatch.
    """
    new_text, status = merge_block(existing, protocol)
    lines: list[str] = []

    if status == "conflict" and adopt:
        wrapped = adopt_unmanaged_protocol(existing)
        if wrapped is not None:
            new_text, status = merge_block(wrapped, protocol)
            status = "adopted"
        else:
            lines.append(
                f"  WARNING: --adopt found no markdown heading to wrap in {target} "
                "— wrap it in "
                f"{BEGIN_MARKER} / {END_MARKER} by hand instead."
            )

    if status == "current":
        lines.append(f"  no change     {target}  (managed block already current)")
        return lines, status

    if status == "conflict":
        lines.append(f"  skip          {target}  (already has its own memory protocol)")
        lines.append(
            "  WARNING: appending a second set of memory rules to a file loaded in "
            "every session would leave two possibly-conflicting protocols. Nothing "
            "was written. Re-run with --adopt to wrap the existing section in "
            f"{BEGIN_MARKER} / {END_MARKER} automatically, or do it by hand — "
            "future runs then refresh it in place."
        )
        return lines, status

    assert new_text is not None  # status is 'updated', 'appended', or 'adopted'
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Refresh and adopt both rewrite bytes that already existed, so both need
        # a safety copy. Appending leaves every prior byte in place and needs none.
        if status in ("updated", "adopted"):
            target.with_suffix(target.suffix + ".bak").write_text(existing)
        target.write_text(new_text)

    if status == "adopted":
        verb = "would adopt" if dry_run else "adopted"
        detail = "wrapped your existing section, then refreshed it to the managed template"
    elif status == "updated":
        verb = "would refresh" if dry_run else "refreshed"
        detail = "managed block only; your own rules untouched"
    else:
        verb = "would append" if dry_run else "appended"
        detail = "managed block added below your existing rules" if existing.strip() else "new file"
    lines.append(f"  {verb:<13} {target}  ({detail})")
    if status in ("updated", "adopted") and not dry_run:
        lines.append(f"  backup        {target.name}.bak")
    return lines, status


def init_global_rules(*, dry_run: bool, adopt: bool = False, path: Path | None = None) -> list[str]:
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

    lines, status = _apply_protocol(target, existing, protocol, dry_run=dry_run, adopt=adopt)
    results.extend(lines)
    if status == "conflict":
        return results

    results.append("")
    results.append(
        "Re-run `gingugu init` after upgrading gingugu to refresh the protocol in place."
    )
    return results


REPO_RULES_FILES = ("CLAUDE.md", "AGENTS.md")


def init_repo_rules(target: Path, *, dry_run: bool, adopt: bool = False) -> list[str]:
    """Merge the managed protocol block into the repo's own CLAUDE.md / AGENTS.md.

    Only touches files that already exist — creating an AGENTS.md where none
    exists would be presumptuous, and the SessionStart hook already carries the
    protocol into every session regardless of whether a rules file mentions it.
    Same rationale as the global file: hand-authored, loaded every session, no
    ``--force``, and per-repo memory sections are not uniform (a shared block
    can only ever carry the generic protocol — repo-specific rules outside the
    markers can still drift).
    """
    protocol = read_template("rules_protocol.md.tmpl")
    results: list[str] = ["Repo rules files (CLAUDE.md / AGENTS.md):"]
    found = False
    for name in REPO_RULES_FILES:
        rules_path = target / name
        if not rules_path.exists():
            continue
        found = True
        existing = safe_read(rules_path)
        lines, _ = _apply_protocol(rules_path, existing, protocol, dry_run=dry_run, adopt=adopt)
        results.extend(lines)
    if not found:
        results.append(f"  none present ({', '.join(REPO_RULES_FILES)}); nothing to do")
    return results
