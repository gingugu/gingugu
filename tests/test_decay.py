"""Tests for decay scoring and staleness detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gingugu import decay


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def test_freshness_decays_with_time() -> None:
    assert decay.freshness(0, 0.05) == 1.0
    older = decay.freshness(30, 0.05)
    newer = decay.freshness(5, 0.05)
    assert 0.0 < older < newer <= 1.0


def test_access_score_saturates() -> None:
    assert decay.access_score(0) == 0.0
    assert decay.access_score(1) > 0.0
    assert decay.access_score(10_000) == 1.0
    assert decay.access_score(5) < decay.access_score(40)


def test_confidence_score_ordering() -> None:
    assert decay.confidence_score("verified") == 1.0
    assert decay.confidence_score("inferred") == 0.7
    assert decay.confidence_score("stale") == 0.3
    assert decay.confidence_score("deprecated") == 0.0


def test_reference_timestamp_coalesce() -> None:
    assert decay.reference_timestamp(None, None, "c") == "c"
    assert decay.reference_timestamp(None, "u", "c") == "u"
    assert decay.reference_timestamp("lc", "u", "c") == "lc"


def test_days_between_handles_none() -> None:
    assert decay.days_between(None) == 0.0
    assert decay.days_between(_iso(10)) >= 9.5


def test_composite_additive() -> None:
    weights = {"relevance": 0.25, "freshness": 0.25, "access": 0.25, "confidence": 0.25}
    score = decay.composite_score(
        relevance=1.0, freshness_val=1.0, access_val=1.0, confidence_val=1.0, weights=weights
    )
    assert abs(score - 1.0) < 1e-9


def test_score_memory_fresh_verified_beats_old_inferred() -> None:
    weights = {"relevance": 0.45, "freshness": 0.25, "access": 0.10, "confidence": 0.20}
    fresh = decay.score_memory(
        relevance=0.5,
        last_confirmed=_iso(1),
        updated_at=_iso(1),
        created_at=_iso(1),
        access_count=5,
        confidence="verified",
        weights=weights,
        decay_lambda=0.05,
    )
    old = decay.score_memory(
        relevance=0.5,
        last_confirmed=_iso(200),
        updated_at=_iso(200),
        created_at=_iso(200),
        access_count=0,
        confidence="inferred",
        weights=weights,
        decay_lambda=0.05,
    )
    assert fresh > old


def test_staleness_thresholds() -> None:
    assert decay.is_stale(_iso(100)) is True
    assert decay.is_stale(_iso(10)) is False
    assert decay.suggests_deprecation(_iso(200)) is True
    assert decay.suggests_deprecation(_iso(30)) is False
