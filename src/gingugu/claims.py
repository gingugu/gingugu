"""Extract checkable state claims from memory prose.

A memory that says "PR #10 is open" is making a *claim*. It was true when
written, and the prose is honest history — so the fix for a claim going stale
is never to edit the text. The claim is extracted here as data instead, and
its resolution is recorded alongside it (see ``memory_claims``, migration 005).

That distinction is the whole point. Before this existed, the only way to
record "PR #10 has since merged" was to rewrite the memory or bolt a
``=== STATUS ===`` banner onto it. The corpus grew 160 distinct banner styles
across 37 memories precisely because there was nowhere structured to put it.

Two problems this has to get right, both measured against a 764-memory corpus
before the module was written:

**Refs are not globally unique.** "PR #12" means different objects in
different repos, and memories routinely reference another repo's PRs. So a ref
is qualified by URL first, then by a repo named next to it, then by
``namespace_default`` — the repo a bare ref means in that namespace, which the
one-namespace-per-repo convention makes the namespace's own name.

That default is load-bearing, not a convenience: measured over 764 real
memories, in-text qualification alone yields 26 claims and **zero** usable
contradictions, versus 145 claims and 10 contradictions with it. People write
"PR #20", not "gingugu PR #20", in their own repo's namespace.

The caveat that follows: in a genuinely cross-project namespace, two bare refs
to different repos' PR #12 would key alike. Pass ``namespace_default=None``
there to drop bare refs instead. Contradiction detection additionally requires
both sides to sit in the same namespace, so a mis-key stays contained.

**State is not a clean binary.** "merge HELD" contains both an open and a
resolved word; "doc shipped PR #168" means the PR was *created*, while
"PR #65 SHIPPED" means it merged. ``shipped`` is therefore excluded from the
resolved vocabulary: a missed claim is silent and harmless, a wrong one
teaches the reader to ignore claims entirely.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass

# Explicit URL wins over everything — it names the repo unambiguously.
_URL = re.compile(
    r"https?://(?:www\.)?(?:github|gitlab)\.com/([\w.-]+(?:/[\w.-]+)*?)/"
    r"(?:pull|-/merge_requests|merge_requests)/(\d+)",
    re.I,
)

_REF = re.compile(
    r"(?:(?P<repo>[A-Za-z][\w.-]{2,30})\s+)?"
    r"\b(?P<kind>PR|MR|pull request|merge request)\s*[#!]?(?P<num>\d+)"
)

# Resolved beats open when both appear for the same ref in one memory: a memory
# titled "PR #174 MERGED" that also narrates "Opened + merged same day" is
# asserting resolution. ``shipped`` is deliberately absent — see module docs.
# The negative lookbehinds matter more than they look: "PR #10, open, NOT
# merged yet" contains the word "merged" and would otherwise read as resolved,
# inverting the claim. Resolved is tested before open, so this is the only
# place that negation can be caught.
_RESOLVED = re.compile(
    r"(?<!not )(?<!not yet )(?<!never )(?<!isn't )(?<!wasn't )"
    r"\b(?:merged|landed|released|closed|abandoned|superseded|deleted)\b",
    re.I,
)
_OPEN = re.compile(
    r"\b(?:open|opened|awaiting|pending|unmerged|not\s+(?:yet\s+)?merged|"
    r"held|in\s+review|needs\s+(?:review|merge)|still\s+open)\b",
    re.I,
)

# How far after a ref to look for a state word, and around it for quoting.
_STATE_WINDOW = 90
_QUOTE_WINDOW = 90

STATE_OPEN = "open"
STATE_RESOLVED = "resolved"


@dataclass(frozen=True)
class Claim:
    """One state assertion a memory makes about one repo-qualified ref."""

    kind: str
    ref: str
    state: str
    evidence: str


def _qualify(text: str, match: re.Match[str], namespace_default: str | None) -> str | None:
    """Repo-qualify a ref, or return None when it cannot be done safely.

    Precedence: an explicit URL naming the same number, then a repo word
    immediately preceding the ref, then the namespace's default repo.
    """
    for url in _URL.finditer(text):
        if url.group(2) == match.group("num"):
            return url.group(1).split("/")[-1]
    named = (match.group("repo") or "").strip().rstrip(":,.").lower()
    if named and namespace_default and named == namespace_default.lower():
        return namespace_default
    if named in _KNOWN_REPO_ALIASES:
        return _KNOWN_REPO_ALIASES[named]
    return namespace_default


# Short forms that appear next to refs and unambiguously name a repo.
_KNOWN_REPO_ALIASES: dict[str, str] = {
    "vtp": "VersatermTechPlatform",
    "versatermtechplatform": "VersatermTechPlatform",
}


def _is_quoted(text: str, match: re.Match[str]) -> bool:
    """True when the ref sits inside quotes or backticks — cited, not claimed.

    Only ``"`` and a backtick delimit. A bare ``'`` is a possessive or a
    contraction far more often than a quote, and treating it as one makes
    ordinary prose look like a citation.
    """
    lo = max(0, match.start() - _QUOTE_WINDOW)
    window = text[lo : match.end() + _QUOTE_WINDOW]
    # Position matters: it is not enough that SOME quoted span in the window
    # contains this text. A title's bare `PR #30` must not read as quoted just
    # because a later `"PR #30 open"` citation sits within 90 characters.
    start, end = match.start() - lo, match.end() - lo
    for span in re.finditer(r"[\"`][^\"`\n]{0,160}[\"`]", window):
        if span.start() < start and end <= span.end():
            return True
    return False


def _normalize_kind(raw: str) -> str:
    return "mr" if raw.upper().startswith("M") else "pr"


def extract_claims(
    title: str, content: str, *, namespace_default: str | None = None
) -> list[Claim]:
    """Claims asserted by a memory — at most one per (kind, ref).

    ``title`` is scanned alongside ``content``: a memory titled "PR #174
    MERGED" asserts resolution even when the body narrates the opening.

    ``namespace_default`` is the repo a bare ref most likely means in this
    memory's namespace. Pass None for cross-project namespaces so bare refs
    are dropped rather than mis-keyed.
    """
    text = f"{title}\n{content}"
    best: dict[tuple[str, str], Claim] = {}
    for match in _REF.finditer(text):
        repo = _qualify(text, match, namespace_default)
        if repo is None:
            continue
        if _is_quoted(text, match):
            continue
        window = text[max(0, match.start() - 20) : match.end() + _STATE_WINDOW]
        if _RESOLVED.search(window):
            state = STATE_RESOLVED
        elif _OPEN.search(window):
            state = STATE_OPEN
        else:
            continue  # a bare mention asserts nothing
        kind = _normalize_kind(match.group("kind"))
        key = (kind, f"{repo}#{match.group('num')}")
        existing = best.get(key)
        if existing is not None and not (existing.state == STATE_OPEN and state == STATE_RESOLVED):
            continue
        best[key] = Claim(
            kind=kind,
            ref=key[1],
            state=state,
            evidence=" ".join(window.split())[:200],
        )
    return list(best.values())


# --- persistence ------------------------------------------------------------


def sync_claims(
    conn: sqlite3.Connection,
    memory_id: str,
    claims: list[Claim],
    *,
    now: str,
) -> int:
    """Replace a memory's claim rows with ``claims``. Returns the count written.

    Called on every store and on any title/content update, since the claims a
    memory makes are derived from its text. Resolution state is deliberately
    NOT preserved across a re-sync: if the text changed, what it asserts may
    have changed too, and a stale resolution pointer would be worse than none.
    """
    conn.execute("DELETE FROM memory_claims WHERE memory_id = ?", (memory_id,))
    for claim in claims:
        conn.execute(
            "INSERT INTO memory_claims "
            "(id, memory_id, kind, ref, state, evidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                memory_id,
                claim.kind,
                claim.ref,
                claim.state,
                claim.evidence,
                now,
            ),
        )
    return len(claims)


def find_contradicted(
    conn: sqlite3.Connection,
    *,
    namespace_id: str,
    claims: list[Claim],
    exclude_memory_id: str | None = None,
) -> list[dict]:
    """Memories whose open claim is contradicted by a resolved claim in ``claims``.

    This is the write-time hook: the moment a memory records "PR #10 merged",
    every older memory still asserting "PR #10 open" is knowable. Restricted to
    ``namespace_id`` so a bare-ref mis-key in one namespace cannot reach across
    into another.

    Advisory only — nothing is mutated. The caller decides whether to reconcile.
    """
    resolved = [c for c in claims if c.state == STATE_RESOLVED]
    if not resolved:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for claim in resolved:
        rows = conn.execute(
            "SELECT c.memory_id, c.ref, c.evidence, m.title, m.created_at "
            "FROM memory_claims c JOIN memories m ON m.id = c.memory_id "
            "WHERE c.kind = ? AND c.ref = ? AND c.state = ? "
            "AND c.resolved_at IS NULL AND m.namespace_id = ? "
            "AND m.confidence != 'deprecated'",
            (claim.kind, claim.ref, STATE_OPEN, namespace_id),
        ).fetchall()
        for row in rows:
            if row["memory_id"] == exclude_memory_id or row["memory_id"] in seen:
                continue
            seen.add(row["memory_id"])
            out.append(
                {
                    "id": row["memory_id"],
                    "title": row["title"],
                    "ref": claim.ref,
                    "asserts": STATE_OPEN,
                    "now": STATE_RESOLVED,
                    "their_evidence": row["evidence"],
                    "our_evidence": claim.evidence,
                }
            )
    return out


def mark_resolved(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    ref: str,
    resolved_by: str,
    now: str,
) -> bool:
    """Record that a memory's open claim about ``ref`` is resolved.

    The memory's PROSE IS NEVER TOUCHED. It said "open" and that was true when
    written; this records what we learned later. That separation is the reason
    this table exists instead of a ``=== STATUS ===`` banner convention.
    """
    cur = conn.execute(
        "UPDATE memory_claims SET resolved_state = ?, resolved_by = ?, resolved_at = ? "
        "WHERE memory_id = ? AND ref = ? AND resolved_at IS NULL",
        (STATE_RESOLVED, resolved_by, now, memory_id, ref),
    )
    return cur.rowcount > 0
