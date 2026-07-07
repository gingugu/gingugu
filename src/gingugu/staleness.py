"""Review hints for point-in-time memories (advisory only, never mutating).

A memory like "PR #947 open, waiting on Joe" is true at write time and goes
silently wrong the moment the PR merges. Never-forget is the right lifecycle
model — nothing here demotes, deprecates, or deletes — but the *reader*
deserves a nudge: "this memory describes in-flight state and hasn't been
confirmed in a while — still true?"

Detection is regex-based over content. Two classes of signal:

* **Gated** signals (open-PR references, waiting-on/blocked-on phrasing,
  unmerged branches) only fire once the memory hasn't been confirmed for
  ``REVIEW_HINT_AFTER_DAYS`` — fresh in-flight notes are fine.
* **Ungated** signals carry their own clock: an ``expires 2026-06-29`` whose
  date has passed is flagged immediately.

Consumed by ``memory_context`` (per-surfaced-memory ``review_hints``) and
``memory_stats`` (namespace-wide ``review`` block). See
docs/architecture.md → Review hints.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from .decay import days_between, reference_timestamp

# A gated signal only fires when the memory hasn't been confirmed for this
# many days — in-flight state is expected to be in flight for a sprint or so.
REVIEW_HINT_AFTER_DAYS = 14

_PR_REF = r"(?:PR|MR|pull request|merge request)\s*[#!]?\d+"

# label → pattern. All matching is case-insensitive; the window between the
# reference and the status word is capped so unrelated sentences don't pair up.
_GATED_PATTERNS: dict[str, re.Pattern[str]] = {
    "open-pr-reference": re.compile(
        rf"(?:{_PR_REF}[^.\n]{{0,80}}?\b(?:open|waiting|awaiting|pending|unmerged|"
        rf"not\s+(?:yet\s+)?merged|blocked|needs)\b"
        rf"|\b(?:open|waiting on|awaiting|blocked on)\b[^.\n]{{0,40}}?{_PR_REF})",
        re.IGNORECASE,
    ),
    "waiting-on": re.compile(
        r"\b(?:waiting (?:on|for)|awaiting|blocked (?:on|by))\b", re.IGNORECASE
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


def review_signals(
    content: str,
    *,
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

    # Ungated: the content names its own expiry/observation date.
    for match in _EXPIRES.finditer(content):
        expiry = _parse_date(match.group(1))
        if expiry is not None and expiry < now:
            signals.append("expired-date")
            break
    for match in _AS_OF.finditer(content):
        observed = _parse_date(match.group(1))
        if observed is not None and days_between(observed.isoformat(), now) >= (
            REVIEW_HINT_AFTER_DAYS
        ):
            signals.append("stale-as-of-date")
            break

    # Gated: in-flight-state phrasing, only once the confirmation clock is old.
    anchor = reference_timestamp(last_confirmed, updated_at, created_at)
    if days_between(anchor, now) >= REVIEW_HINT_AFTER_DAYS:
        for label, pattern in _GATED_PATTERNS.items():
            if pattern.search(content):
                signals.append(label)

    return signals
