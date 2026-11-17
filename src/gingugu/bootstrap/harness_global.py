"""The subagent-delegation rules `gingugu harness` installs at user level.

Two pieces, split by cost. The rules go in a managed block in
``~/.claude/CLAUDE.md`` - loaded into every request, so kept short. Their
rationale goes in ``~/.claude/docs/delegation.md``, read only on demand.

Same ownership rules as the memory-protocol block (``global_rules``): only the
text between our markers is ours; a hand-written delegation section is never
replaced without ``--adopt``; any rewrite of existing bytes is backed up first,
to the file as it was before this run (``original``), because ``init`` may have
rewritten the same file - and its ``.bak`` - moments earlier.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import global_rules
from ._files import read_template, write_file
from .global_rules import MARKER_RE
from .harness_shipped import stock_check

DELEGATION_BEGIN = "<!-- BEGIN GINGUGU DELEGATION -->"
DELEGATION_END = "<!-- END GINGUGU DELEGATION -->"
_NOTE = (
    "<!-- Managed by `gingugu harness`. Edits between these markers are\n"
    "     replaced on re-run; put your own rules outside them. -->"
)
_BLOCK_RE = re.compile(
    rf"^{re.escape(DELEGATION_BEGIN)}[ \t]*\r?$.*?^{re.escape(DELEGATION_END)}[ \t]*\r?$",
    re.M | re.S,
)
# Only a top-level section counts as "your delegation section": a nested
# "### Delegation of reviews" under some other heading is not it.
_SECTION_RE = re.compile(r"^## (.*)$", re.M)
_PEER_RE = re.compile(r"^#{1,2} ", re.M)
_FENCE_RE = re.compile(r"^(?:```|~~~)", re.M)


def _block(nl: str) -> str:
    body = read_template("harness/global_delegation_block.md.tmpl").strip()
    return f"{DELEGATION_BEGIN}\n{_NOTE}\n\n{body}\n\n{DELEGATION_END}".replace("\n", nl)


def _in_fence(text: str, pos: int) -> bool:
    """True when ``pos`` sits inside a fenced code block."""
    return len(_FENCE_RE.findall(text, 0, pos)) % 2 == 1


def _own_section(text: str) -> tuple[int, int, str] | None:
    """The user's own delegation section: a ``## ...Delegation...`` heading, to
    the next ``#``/``##`` heading or gingugu marker, whichever comes first."""
    for m in _SECTION_RE.finditer(text):
        title = m.group(1).strip()
        if "delegation" not in title.lower() or _in_fence(text, m.start()):
            continue
        end = len(text)
        for other in _PEER_RE.finditer(text, m.end()):
            if not _in_fence(text, other.start()):
                end = other.start()
                break
        marker = MARKER_RE.search(text, m.end(), end)
        return m.start(), marker.start() if marker else end, title
    return None


def _merge(existing: str, adopt: bool) -> tuple[str | None, str, str]:
    """``(new_text, status, detail)``; ``new_text`` is None when nothing is written.

    Works on the file's own line endings: a CRLF file gets a CRLF block, and
    every byte outside the block is kept as it was.
    """
    nl = "\r\n" if "\r\n" in existing else "\n"
    block = _block(nl)
    m = _BLOCK_RE.search(existing)
    if m:
        new = existing[: m.start()] + block + existing[m.end() :]
        return (None, "current", "") if new == existing else (new, "refreshed", "")
    own = _own_section(existing)
    if own:
        start, end, title = own
        if not adopt:
            return None, "conflict", title
        rest = existing[end:]
        return existing[:start] + block + (nl + nl + rest if rest else nl), "adopted", title
    if not existing.strip():
        return f"{block}{nl}", "appended", ""
    sep = "" if existing.endswith("\n") else nl
    return f"{existing}{sep}{nl}{block}{nl}", "appended", ""


def install_delegation(
    *,
    dry_run: bool,
    adopt: bool = False,
    force: bool = False,
    original: str | None = None,
    claude_md: Path | None = None,
    docs: Path | None = None,
) -> list[str]:
    """Install or refresh the delegation block and its rationale file."""
    path = claude_md or global_rules.global_claude_md()
    docs = docs or path.parent / "docs" / "delegation.md"
    # Bytes, not read_text(): CRLF survives and the .bak is the file as it was.
    # A file that exists but cannot be read raises here, before anything is written.
    existing = path.read_bytes().decode() if path.exists() else ""
    new, status, detail = _merge(existing, adopt)
    results: list[str] = []
    write_file(
        docs,
        read_template("harness/docs/delegation.md.tmpl"),
        # Adopting your delegation section hands over your rationale file too
        # (backed up) - on that run only, not on every --adopt.
        force=force or status == "adopted",
        dry_run=dry_run,
        results=results,
        is_stock=stock_check("docs/delegation.md"),
    )
    if status == "current":
        results.append(f"  delegation block already current (no change) {path}")
        return results
    if status == "conflict":
        results.append(f"  skip   {path}  (has its own '{detail}' section)")
        results.append(
            "  WARNING: your own delegation rules were left untouched, so the managed "
            "block was not added. Re-run with --adopt to hand that section to "
            "gingugu (backed up first); future runs then refresh it in place."
        )
        return results

    backup = status in ("refreshed", "adopted")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        if backup:
            bak = original if original is not None else existing
            path.with_suffix(path.suffix + ".bak").write_bytes(bak.encode())
        path.write_bytes((new or "").encode())
    verb = {"appended": "append", "refreshed": "refresh", "adopted": "adopt"}[status]
    verb = f"would {verb}" if dry_run else ("adopted" if verb == "adopt" else f"{verb}ed")
    note = f" (your '{detail}' section)" if status == "adopted" else ""
    tail = f"; backed up to {path.name}.bak" if backup and not dry_run else ""
    results.append(f"  {verb} delegation block in {path}{note}{tail}")
    return results
