"""Review hints for point-in-time memories (advisory only, never mutating).

A memory like "PR #947 open, waiting on Joe" is true at write time and goes
silently wrong the moment the PR merges. Never-forget is the right lifecycle
model — nothing here demotes, deprecates, or deletes — but the *reader*
deserves a nudge: "this memory describes in-flight state and hasn't been
confirmed in a while — still true?"

Detection is regex-based over content. Two classes of signal:

* **Gated** signals (open-PR references, waiting-on/blocked-on phrasing,
  unmerged branches) only fire once the memory hasn't been confirmed for
  ``REVIEW_HINT_AFTER_DAYS`` — fresh in-flight notes are fine — and never
  on timeless types (``_TIMELESS_TYPES``), whose prose is reference
  material, not status.
* **Ungated** signals carry their own clock: an ``expires 2026-06-29`` whose
  date has passed is flagged immediately.

Consumed by ``memory_context`` (per-surfaced-memory ``review_hints``) and
``memory_stats`` (namespace-wide ``review`` block). See
docs/architecture.md → Review hints.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from .decay import days_between, reference_timestamp

# A gated signal only fires when the memory hasn't been confirmed for this
# many days — in-flight state is expected to be in flight for a sprint or so.
REVIEW_HINT_AFTER_DAYS = 14

# Distilled-wisdom types never carry in-flight state: a pattern that says
# "apps blocked on disk I/O" or a preference quoting "waiting on Joe" as an
# example is timeless prose, not a status note. Gated signals skip these
# types; ungated ones (expired/as-of dates) still apply — a date is a date.
# Mirrors the cross-namespace wisdom bucket in context.py.
_TIMELESS_TYPES = frozenset({"pattern", "preference"})

# Leading \b keeps the alternation from matching inside longer words ("GDPR").
_PR_REF = r"\b(?:PR|MR|pull request|merge request)\s*[#!]?\d+"

# label → pattern. All matching is case-insensitive; the window between the
# reference and the status word is capped so unrelated sentences don't pair up.
# The waiting-on lookbehinds skip past-tense narrative ("was blocked on…"):
# a resolved story is history, not in-flight state.
_GATED_PATTERNS: dict[str, re.Pattern[str]] = {
    "open-pr-reference": re.compile(
        rf"(?:{_PR_REF}[^.\n]{{0,80}}?\b(?:open|waiting|awaiting|pending|unmerged|"
        rf"not\s+(?:yet\s+)?merged|blocked|needs)\b"
        rf"|\b(?:open|waiting on|awaiting|blocked on)\b[^.\n]{{0,40}}?{_PR_REF})",
        re.IGNORECASE,
    ),
    "waiting-on": re.compile(
        r"(?<!was )(?<!were )\b(?:waiting (?:on|for)|awaiting|blocked (?:on|by))\b",
        re.IGNORECASE,
    ),
    "unmerged-branch": re.compile(
        r"\bbranch\b[^.\n]{0,60}\b(?:not\s+(?:yet\s+)?merged|unmerged|still open)\b",
        re.IGNORECASE,
    ),
}

_EXPIRES = re.compile(r"\bexpires?\s+(?:on\s+)?(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_AS_OF = re.compile(r"\bas of\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)


def _parse_date(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    except ValueError:
        return None


def _latest_date(pattern: re.Pattern[str], content: str) -> datetime | None:
    """The newest date a pattern names in the content, or None.

    Only the newest occurrence matters: "expires 2026-01-01; RENEWED: expires
    2027-01-01" is current, not expired.
    """
    dates = [d for m in pattern.finditer(content) if (d := _parse_date(m.group(1)))]
    return max(dates) if dates else None


def review_signals(
    content: str,
    *,
    memory_type: str | None = None,
    last_confirmed: str | None = None,
    updated_at: str | None = None,
    created_at: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Return the review-signal labels that fire for a memory, oldest-clock first.

    Empty list means "no nudge". Purely advisory — callers must never mutate
    a memory based on this.
    """
    now = now or datetime.now(UTC)
    signals: list[str] = []

    # Ungated: the content names its own expiry/observation date. A bare
    # YYYY-MM-DD parses to midnight, so an expiry counts only once its whole
    # day has passed — "expires <today>" is still valid.
    expiry = _latest_date(_EXPIRES, content)
    if expiry is not None and expiry + timedelta(days=1) <= now:
        signals.append("expired-date")
    observed = _latest_date(_AS_OF, content)
    if observed is not None and (now - observed).days >= REVIEW_HINT_AFTER_DAYS:
        signals.append("stale-as-of-date")

    # Gated: in-flight-state phrasing, only once the confirmation clock is old
    # — and never on timeless types, where the phrasing is reference material.
    if memory_type in _TIMELESS_TYPES:
        return signals
    anchor = reference_timestamp(last_confirmed, updated_at, created_at)
    if days_between(anchor, now) >= REVIEW_HINT_AFTER_DAYS:
        for label, pattern in _GATED_PATTERNS.items():
            if pattern.search(content):
                signals.append(label)

    return signals
