"""Decay scoring and staleness detection.

Composite score blends lexical relevance with temporal and trust signals:

    score = w_r·relevance + w_f·freshness + w_a·access + w_c·confidence

All components are normalized to [0, 1] and blended **additively** so one weak
factor can't zero out the score. See docs/architecture.md → Decay Scoring.
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

# Staleness thresholds (days).
STALE_AFTER_DAYS = 90
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
    """exp(-λ · days_since_confirmed) in (0, 1]."""
    return math.exp(-lambda_ * max(0.0, days_since))


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


def is_stale(last_accessed: str | None, now: datetime | None = None) -> bool:
    """True if not accessed within STALE_AFTER_DAYS."""
    return days_between(last_accessed, now) >= STALE_AFTER_DAYS


def suggests_deprecation(last_confirmed: str | None, now: datetime | None = None) -> bool:
    """True if not confirmed within DEPRECATE_SUGGEST_AFTER_DAYS."""
    return days_between(last_confirmed, now) >= DEPRECATE_SUGGEST_AFTER_DAYS
