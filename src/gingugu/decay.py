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
    """Null-safe freshness anchor: COALESCE(last_confirmed, updated_at, created_at)."""
    return last_confirmed or updated_at or created_at


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


def composite_score(
    *,
    relevance: float,
    freshness_val: float,
    access_val: float,
    confidence_val: float,
    weights: dict[str, float],
) -> float:
    """Additive blend of the four normalized components."""
    return (
        weights["relevance"] * relevance
        + weights["freshness"] * freshness_val
        + weights["access"] * access_val
        + weights["confidence"] * confidence_val
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
    anchor = reference_timestamp(last_confirmed, updated_at, created_at)
    fresh = freshness(days_between(anchor, now), decay_lambda)
    return composite_score(
        relevance=relevance,
        freshness_val=fresh,
        access_val=access_score(access_count),
        confidence_val=confidence_score(confidence),
        weights=weights,
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
