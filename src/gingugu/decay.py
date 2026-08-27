"""Scoring and memory-lifecycle signals.

Composite score blends lexical relevance with temporal and trust signals:

    score = w_r·relevance + w_f·freshness + w_a·access + w_c·confidence

All components are normalized to [0, 1] and blended **additively** so one weak
factor can't zero out the score. See docs/architecture.md → Scoring.

Lifecycle philosophy: a robot brain never forgets. Time alone never destroys
trust or retrievability — it only makes a memory *dormant*, not *stale*.
Freshness therefore has a floor (it never decays to zero), confidence (trust)
is the dominant standalone signal, and dormancy is reported, never auto-applied.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from .models import Confidence

# Confidence → trust weight (see architecture component table).
_CONFIDENCE_WEIGHT: dict[str, float] = {
    Confidence.VERIFIED.value: 1.0,
    Confidence.INFERRED.value: 0.7,
    Confidence.STALE.value: 0.3,
    Confidence.DEPRECATED.value: 0.0,
}

# Access saturation: log(count+1)/log(_ACCESS_SATURATION) capped at 1.0.
_ACCESS_SATURATION = 50

# Freshness never decays below this floor: a 5-year-old verified memory is
# dormant, not worthless. Recency is a gentle tiebreaker, not an eraser.
FRESHNESS_FLOOR = 0.35

# Dormancy threshold (days). Untouched longer than this = dormant (a *signal*
# surfaced in stats, never an automatic confidence change). STALE_AFTER_DAYS
# is kept as a backward-compatible alias.
DORMANT_AFTER_DAYS = 90
STALE_AFTER_DAYS = DORMANT_AFTER_DAYS
DEPRECATE_SUGGEST_AFTER_DAYS = 180


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def days_between(earlier: str | None, now: datetime | None = None) -> float:
    """Days from ``earlier`` (ISO-8601) until ``now`` (default: UTC now)."""
    start = _parse(earlier)
    if start is None:
        return 0.0
    now = now or datetime.now(UTC)
    return max(0.0, (now - start).total_seconds() / 86400.0)


def reference_timestamp(
    last_confirmed: str | None, updated_at: str | None, created_at: str | None
) -> str | None:
    """Null-safe freshness anchor: the LATEST of the three instants.

    Deliberately a max, **not** a COALESCE. COALESCE returns the first non-null,
    so a content edit made after the last confirmation was silently discarded
    and the memory got scored, spread-sorted and staleness-checked off the
    older instant. Unparseable candidates are skipped rather than crashing a
    read; if none parse, fall back to first-non-null so the caller still gets
    whatever the row has.
    """
    parsed = [
        (dt, raw)
        for raw in (last_confirmed, updated_at, created_at)
        if (dt := _parse(raw)) is not None
    ]
    if not parsed:
        return last_confirmed or updated_at or created_at
    return max(parsed, key=lambda pair: pair[0])[1]


def relative_age(instant: str | None, now: datetime | None = None) -> str | None:
    """Human-readable interval since ``instant`` (ISO-8601), derived at call time.

    STORE THE INSTANT, DERIVE THE INTERVAL. The result is **never** persisted:
    a stored "6 days ago" is wrong the moment the world moves on, which is the
    exact bug class ``memory_claims`` and ``review_hints`` exist to catch. Same
    lifecycle as ``score`` and ``credentials.expiry_status`` — computed per read,
    never written back. Returns ``None`` for an unparseable/missing instant.
    """
    start = _parse(instant)
    if start is None:
        return None
    seconds = max(0.0, ((now or datetime.now(UTC)) - start).total_seconds())
    minutes = seconds / 60.0
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)} minutes ago"
    hours = minutes / 60.0
    if hours < 24:
        return _plural(int(hours), "hour")
    days = hours / 24.0
    if days < 7:
        return _plural(int(days), "day")
    if days < 60:
        return _plural(int(days / 7), "week")
    if days < 365:
        return _plural(int(days / 30), "month")
    return _plural(int(days / 365), "year")


def age_label(
    created_at: str | None,
    anchor: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Payload-facing age: how long the memory has existed, plus how recently it
    was maintained when those differ.

    ``"7 weeks ago"`` for an untouched memory; ``"7 weeks ago (updated just
    now)"`` for one rewritten since it was written. The parenthetical costs ~4
    tokens and appears **only** on maintained memories — exactly where the
    distinction carries information, because "durable AND current" is a
    stronger signal than either half. Without it, a memory rewritten minutes
    ago reads as weeks stale, which defeats the one job ``age`` exists to do.

    ``anchor`` is the freshness anchor (``reference_timestamp``) — the same
    instant the scorer, the spread-neighbour sort and staleness already use, so
    the payload no longer disagrees with the ranking.
    """
    base = relative_age(created_at, now)
    if base is None:
        return None
    born, maintained = _parse(created_at), _parse(anchor)
    if born is None or maintained is None or maintained <= born:
        return base
    updated = relative_age(anchor, now)
    # Identical renderings ("just now (updated just now)") add noise, not signal.
    if updated is None or updated == base:
        return base
    return f"{base} (updated {updated})"


def _plural(value: int, unit: str) -> str:
    return f"1 {unit} ago" if value == 1 else f"{value} {unit}s ago"


def freshness(days_since: float, lambda_: float) -> float:
    """Floored exponential recency in [FRESHNESS_FLOOR, 1].

    ``floor + (1 - floor)·exp(-λ · days)``. Fresh memories score ~1.0; ancient
    ones asymptote to ``FRESHNESS_FLOOR`` instead of zero — dormancy must never
    push a trusted memory out of reach.
    """
    raw = math.exp(-lambda_ * max(0.0, days_since))
    return FRESHNESS_FLOOR + (1.0 - FRESHNESS_FLOOR) * raw


def access_score(access_count: int) -> float:
    """log(count+1)/log(saturation), capped at 1.0."""
    if access_count <= 0:
        return 0.0
    return min(1.0, math.log(access_count + 1) / math.log(_ACCESS_SATURATION))


def confidence_score(confidence: str) -> float:
    return _CONFIDENCE_WEIGHT.get(confidence, 0.0)


def composite_parts(
    *,
    relevance: float,
    freshness_val: float,
    access_val: float,
    confidence_val: float,
    weights: dict[str, float],
) -> dict[str, float]:
    """The four *weighted* terms of the composite, keyed by component.

    Weighted contributions, not raw components, because these sum to the
    score: reading them answers "which term put this memory here?" without
    the caller knowing the configured weights. The raw component divides
    back out; the ranking consequence does not.
    """
    return {
        "relevance": weights["relevance"] * relevance,
        "freshness": weights["freshness"] * freshness_val,
        "access": weights["access"] * access_val,
        "confidence": weights["confidence"] * confidence_val,
    }


def composite_score(
    *,
    relevance: float,
    freshness_val: float,
    access_val: float,
    confidence_val: float,
    weights: dict[str, float],
) -> float:
    """Additive blend of the four normalized components.

    Summed from ``composite_parts`` so a reported breakdown and the score it
    explains can never disagree - a breakdown that does not add up to the
    number it explains is worse than none at all.
    """
    return sum(
        composite_parts(
            relevance=relevance,
            freshness_val=freshness_val,
            access_val=access_val,
            confidence_val=confidence_val,
            weights=weights,
        ).values()
    )


def score_parts(
    *,
    relevance: float,
    last_confirmed: str | None,
    updated_at: str | None,
    created_at: str | None,
    access_count: int,
    confidence: str,
    weights: dict[str, float],
    decay_lambda: float,
    now: datetime | None = None,
) -> dict[str, float]:
    """The weighted terms behind a memory row's composite score.

    Same inputs and arithmetic as ``score_memory``, which is summed from this.
    Callers wanting both should call this once and sum it, not call both.
    """
    anchor = reference_timestamp(last_confirmed, updated_at, created_at)
    fresh = freshness(days_between(anchor, now), decay_lambda)
    return composite_parts(
        relevance=relevance,
        freshness_val=fresh,
        access_val=access_score(access_count),
        confidence_val=confidence_score(confidence),
        weights=weights,
    )


def score_memory(
    *,
    relevance: float,
    last_confirmed: str | None,
    updated_at: str | None,
    created_at: str | None,
    access_count: int,
    confidence: str,
    weights: dict[str, float],
    decay_lambda: float,
    now: datetime | None = None,
) -> float:
    """Compute the full composite score for a memory row."""
    return sum(
        score_parts(
            relevance=relevance,
            last_confirmed=last_confirmed,
            updated_at=updated_at,
            created_at=created_at,
            access_count=access_count,
            confidence=confidence,
            weights=weights,
            decay_lambda=decay_lambda,
            now=now,
        ).values()
    )


def is_dormant(last_accessed: str | None, now: datetime | None = None) -> bool:
    """True if not accessed within DORMANT_AFTER_DAYS.

    Dormancy is informational only — it never changes a memory's confidence.
    A dormant memory is resting, not rotting; recall (directly or via spreading
    activation through related memories) wakes it back up.
    """
    return days_between(last_accessed, now) >= DORMANT_AFTER_DAYS


# Backward-compatible alias. "Stale" framing is deprecated in favour of
# "dormant"; the function no longer implies any confidence demotion.
is_stale = is_dormant


def suggests_deprecation(last_confirmed: str | None, now: datetime | None = None) -> bool:
    """True if not confirmed within DEPRECATE_SUGGEST_AFTER_DAYS."""
    return days_between(last_confirmed, now) >= DEPRECATE_SUGGEST_AFTER_DAYS
