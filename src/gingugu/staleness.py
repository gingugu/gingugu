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

# What a genuine wait points at: a person, a PR/MR, a ticket key, or a named
# artifact. A wait on a *technical event* names none of these — "waiting for
# EOF", "waiting for the init container image pull" are descriptions of a
# mechanism, not a status. Deliberately case-sensitive: `[A-Z][a-z]{2,}` is
# meant to catch a capitalised proper noun (Joe, Baskar), not any word.
_NAMED_AGENT = re.compile(
    r"(?:PR|MR|pull request|merge request)\s*[#!]?\d+"
    r"|\b[A-Z][a-z]{2,}\b"
    r"|\b[A-Z]{2,}-\d+\b"
    r"|\b(?:key|sign-?off|approval|go-ahead|response|reply)\b"
)
_AGENT_WINDOW = 60

# How far to look around a match when deciding whether it sits inside a quote.
_QUOTE_WINDOW = 90


def _is_quoted(content: str, match: re.Match[str]) -> bool:
    """True when the match sits inside quotes or backticks.

    Quoted text is almost always being *cited* rather than asserted — a bug
    report quoting "expire 2026-06-29", or a note narrating that a doc still
    said "awaiting merge" after the merge landed. The memory is describing
    the phrase, not claiming the state.
    """
    window = content[max(0, match.start() - _QUOTE_WINDOW) : match.end() + _QUOTE_WINDOW]
    literal = re.escape(match.group(0))
    # Only " and ` count as delimiters. A bare ' is far more often a possessive
    # or contraction ("Boomtastic's", "PR'd") than a quote, and treating it as
    # one makes ordinary prose look cited.
    return bool(
        re.search(
            rf"[\"`][^\"`\n]{{0,80}}{literal}[^\"`\n]{{0,80}}[\"`]",
            window,
            re.IGNORECASE,
        )
    )


def _asserts_live_state(content: str, pattern: re.Pattern[str]) -> bool:
    """True when at least one of ``pattern``'s matches reads as a live claim.

    A match counts only if it is unquoted *and* names an agent the wait is on.
    Both filters are needed: quoting alone misses "waiting for EOF", and the
    agent check alone misses a quoted "awaiting merge".
    """
    for match in pattern.finditer(content):
        if _is_quoted(content, match):
            continue
        if _NAMED_AGENT.search(content[match.end() : match.end() + _AGENT_WINDOW]):
            return True
    return False


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
    dates = [
        d
        for m in pattern.finditer(content)
        if not _is_quoted(content, m) and (d := _parse_date(m.group(1)))
    ]
    return max(dates) if dates else None


def _confirmed_after(last_confirmed: str | None, moment: datetime) -> bool:
    """True when the memory was explicitly reconfirmed after ``moment``.

    ``last_confirmed`` only advances on a deliberate write (storage.update
    stamps it when confidence is set to verified) — reads never touch it. So a
    confirmation that postdates a named expiry is a human saying "yes, I know,
    and this record is still right": the outcome is already in the body.

    The comparison is against the END of ``moment``'s day. A bare YYYY-MM-DD
    parses to midnight, and a memory written on day X almost always says "as of
    day X" — comparing against midnight would make every such memory suppress
    its own signal the moment it was written.
    """
    if not last_confirmed:
        return False
    try:
        confirmed = datetime.fromisoformat(last_confirmed)
    except ValueError:
        return False
    if confirmed.tzinfo is None:
        confirmed = confirmed.replace(tzinfo=UTC)
    return confirmed >= moment + timedelta(days=1)


def _has_unquoted_match(content: str, pattern: re.Pattern[str]) -> bool:
    """True when ``pattern`` matches at least once outside quotes/backticks."""
    return any(not _is_quoted(content, m) for m in pattern.finditer(content))


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
    # A date the memory was reconfirmed *after* is already reconciled: the
    # record's own body carries the outcome, so nudging about it forever is noise.
    expiry = _latest_date(_EXPIRES, content)
    if (
        expiry is not None
        and expiry + timedelta(days=1) <= now
        and not _confirmed_after(last_confirmed, expiry)
    ):
        signals.append("expired-date")
    observed = _latest_date(_AS_OF, content)
    if (
        observed is not None
        and (now - observed).days >= REVIEW_HINT_AFTER_DAYS
        and not _confirmed_after(last_confirmed, observed)
    ):
        signals.append("stale-as-of-date")

    # Gated: in-flight-state phrasing, only once the confirmation clock is old
    # — and never on timeless types, where the phrasing is reference material.
    if memory_type in _TIMELESS_TYPES:
        return signals
    anchor = reference_timestamp(last_confirmed, updated_at, created_at)
    if days_between(anchor, now) >= REVIEW_HINT_AFTER_DAYS:
        for label, pattern in _GATED_PATTERNS.items():
            # "waiting-on" is the only signal whose phrasing also occurs in
            # ordinary technical prose ("waiting for EOF"), so it additionally
            # requires the wait to name an agent. The other two carry their own
            # subject — a PR ref, a branch — and only need the quoting filter.
            fires = (
                _asserts_live_state(content, pattern)
                if label == "waiting-on"
                else _has_unquoted_match(content, pattern)
            )
            if fires:
                signals.append(label)

    return signals
