"""Per-hit score breakdown: the terms must add up to the score they explain."""

from __future__ import annotations

import json

import pytest

from gingugu import decay

WEIGHTS = {"relevance": 0.4, "freshness": 0.3, "access": 0.1, "confidence": 0.2}


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "explain.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "explain")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _store(server, title: str, content: str, type: str = "preference") -> str:
    res = _payload(
        await server.call_tool(
            "memory_store",
            {"title": title, "content": content, "type": type, "confidence": "verified"},
        )
    )
    assert res["ok"]
    return res["memory"]["id"]


def test_parts_sum_to_score() -> None:
    kwargs = dict(
        relevance=0.8,
        last_confirmed="2026-08-01T12:00:00+00:00",
        updated_at="2026-08-01T12:00:00+00:00",
        created_at="2026-07-01T12:00:00+00:00",
        access_count=12,
        confidence="verified",
        weights=WEIGHTS,
        decay_lambda=0.01,
    )
    parts = decay.score_parts(**kwargs)
    assert set(parts) == {"relevance", "freshness", "access", "confidence"}
    assert sum(parts.values()) == pytest.approx(decay.score_memory(**kwargs))


def test_parts_are_weighted_contributions_not_raw_components() -> None:
    """A term is component × weight, so the terms are directly comparable."""
    parts = decay.composite_parts(
        relevance=1.0,
        freshness_val=1.0,
        access_val=1.0,
        confidence_val=1.0,
        weights=WEIGHTS,
    )
    assert parts == WEIGHTS


def test_zero_weight_term_reports_zero_not_missing() -> None:
    """A disabled signal still gets a term - 'contributed nothing' is an answer."""
    weights = {**WEIGHTS, "access": 0.0}
    parts = decay.score_parts(
        relevance=0.5,
        last_confirmed=None,
        updated_at=None,
        created_at="2026-08-01T12:00:00+00:00",
        access_count=99,
        confidence="verified",
        weights=weights,
        decay_lambda=0.01,
    )
    assert parts["access"] == 0.0


@pytest.mark.asyncio
async def test_recall_omits_breakdown_by_default(server) -> None:
    await _store(server, "harbour rules", "the harbour rules for docking")

    res = _payload(await server.call_tool("memory_recall", {"query": "harbour"}))
    assert res["memories"]
    assert "score" in res["memories"][0]
    assert "score_breakdown" not in res["memories"][0]


@pytest.mark.asyncio
async def test_recall_breakdown_sums_to_reported_score(server) -> None:
    await _store(server, "harbour rules", "the harbour rules for docking")

    res = _payload(await server.call_tool("memory_recall", {"query": "harbour", "explain": True}))
    hit = res["memories"][0]
    breakdown = hit["score_breakdown"]
    assert set(breakdown) == {"relevance", "freshness", "access", "confidence"}
    # Both sides are rounded to 4dp independently, so allow one ulp of that.
    assert sum(breakdown.values()) == pytest.approx(hit["score"], abs=1e-3)


@pytest.mark.asyncio
async def test_breakdown_survives_compact_mode(server) -> None:
    """Compact drops bookkeeping, not the diagnostic the caller explicitly asked for."""
    await _store(server, "harbour rules", "the harbour rules for docking")

    res = _payload(
        await server.call_tool(
            "memory_recall", {"query": "harbour", "explain": True, "compact": True}
        )
    )
    hit = res["memories"][0]
    assert "summary" in hit
    assert "score_breakdown" in hit


@pytest.mark.asyncio
async def test_search_ids_fetch_has_no_breakdown(server) -> None:
    """An id fetch is not a ranking, so there is nothing to decompose."""
    mid = await _store(server, "harbour rules", "the harbour rules for docking")

    res = _payload(await server.call_tool("memory_search", {"ids": mid, "explain": True}))
    assert res["memories"][0].get("score_breakdown") is None


@pytest.mark.asyncio
async def test_context_reports_type_boost_as_its_own_term(server) -> None:
    """The architecture/decision boost is a real term, and it must show as one."""
    await _store(server, "the storage decision", "we chose sqlite", type="decision")

    res = _payload(
        await server.call_tool(
            "memory_context", {"namespace": "explain", "task_hint": "storage", "explain": True}
        )
    )
    boosted = [m for m in res["memories"] if m["type"] == "decision"]
    assert boosted, "expected the decision memory in context"
    breakdown = boosted[0]["score_breakdown"]
    assert breakdown["type_boost"] == pytest.approx(0.1)
    assert sum(breakdown.values()) == pytest.approx(boosted[0]["score"], abs=1e-3)


@pytest.mark.asyncio
async def test_pinned_memory_carries_no_breakdown(server) -> None:
    """Pins bypass ranking entirely - inventing terms for them would be a lie."""
    mid = await _store(server, "never forget this", "the one rule")
    assert _payload(await server.call_tool("memory_update", {"memory_id": mid, "pinned": True}))[
        "ok"
    ]

    res = _payload(
        await server.call_tool("memory_context", {"namespace": "explain", "explain": True})
    )
    pin = next(m for m in res["memories"] if m["id"] == mid)
    assert pin.get("pinned") is True
    assert "score_breakdown" not in pin


@pytest.mark.asyncio
async def test_synthetic_relevance_is_visible_as_a_flat_term(server) -> None:
    """The recency bucket scores on a constant relevance; the breakdown shows it.

    This is the diagnostic the instrument exists for: several hits sharing one
    identical relevance term did not match the hint - they were selected for
    recency, and no amount of matching would have moved that term.
    """
    for i in range(3):
        await _store(server, f"unrelated note {i}", f"body {i}")

    res = _payload(
        await server.call_tool(
            "memory_context",
            {"namespace": "explain", "task_hint": "something else entirely", "explain": True},
        )
    )
    relevances = {m["score_breakdown"]["relevance"] for m in res["memories"]}
    assert len(relevances) == 1
