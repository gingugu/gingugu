"""Tests for bulk ``memory_relate``: several reviewed edges in one call.

A normal session writes relations in runs - a new memory supersedes one, was
caused by another, belongs under a third - and each was its own round trip for
a four-line ack. The batch sends them together. The one design call it pins is
**all-or-nothing**: a half-applied relation set is the graph state hardest to
notice and hardest to repair, so the whole set is validated before anything is
written and applied in one transaction.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "relate.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    from gingugu.server import build_server

    return build_server()


async def _store(server, title: str) -> str:
    payload = {"title": title, "content": f"content of {title}", "type": "fact"}
    return _payload(await server.call_tool("memory_store", payload))["memory"]["id"]


async def _relate(server, **kwargs) -> dict:
    return _payload(await server.call_tool("memory_relate", kwargs))


async def _edge_count(server) -> int:
    return _payload(await server.call_tool("memory_edges", {}))["total"]


async def _four(server) -> tuple[str, str, str, str]:
    return (
        await _store(server, "alpha"),
        await _store(server, "beta"),
        await _store(server, "gamma"),
        await _store(server, "delta"),
    )


# --- the happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_creates_every_edge_in_one_call(server) -> None:
    a, b, c, d = await _four(server)
    result = await _relate(
        server,
        edges=[
            {"source_id": a, "target_id": b, "relation_type": "supersedes"},
            {"source_id": a, "target_id": c, "relation_type": "caused_by"},
            {"source_id": a, "target_id": d, "relation_type": "child_of"},
        ],
    )
    assert result["ok"] is True
    assert result["processed"] == 3
    assert result["outcomes"] == {"created": 3}
    assert [r["relation_type"] for r in result["results"]] == [
        "supersedes",
        "caused_by",
        "child_of",
    ]
    assert await _edge_count(server) == 3


@pytest.mark.asyncio
async def test_batch_reports_an_existing_edge_instead_of_pretending_to_create_it(server) -> None:
    """Relate is idempotent on (source, target, type). A batch must say which
    edges were already there, or a caller cannot tell a no-op from a write."""
    a, b, c, _ = await _four(server)
    await _relate(server, source_id=a, target_id=b, relation_type="supersedes")

    result = await _relate(
        server,
        edges=[
            {"source_id": a, "target_id": b, "relation_type": "supersedes"},
            {"source_id": a, "target_id": c, "relation_type": "caused_by"},
        ],
    )
    assert result["ok"] is True
    assert result["outcomes"] == {"exists": 1, "created": 1}
    assert [r["outcome"] for r in result["results"]] == ["exists", "created"]
    assert await _edge_count(server) == 2


@pytest.mark.asyncio
async def test_single_edge_call_is_unchanged(server) -> None:
    a, b, _, _ = await _four(server)
    result = await _relate(server, source_id=a, target_id=b, relation_type="caused_by")
    assert result["ok"] is True
    assert result["relation"] == {"source_id": a, "target_id": b, "relation_type": "caused_by"}


# --- all-or-nothing validation -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_op, fragment",
    [
        ({"target_id": "__B__", "relation_type": "caused_by"}, "source_id"),
        ({"source_id": "__A__", "relation_type": "caused_by"}, "target_id"),
        ({"source_id": "__A__", "target_id": "__B__"}, "relation_type"),
        ({"source_id": "__A__", "target_id": "__B__", "relation_type": "vibes"}, "vibes"),
        ({"source_id": "__A__", "target_id": "__A__", "relation_type": "caused_by"}, "self"),
        (
            {"source_id": "__A__", "target_id": "no-such-id", "relation_type": "caused_by"},
            "no-such-id",
        ),
        ("not an object", "object"),
    ],
)
async def test_one_bad_op_rejects_the_whole_batch_and_names_it(server, bad_op, fragment) -> None:
    """Every failure names the offending index, and the good op before it was
    never written - including the not-found case, which is only discoverable
    by looking the memory up."""
    a, b, c, _ = await _four(server)
    if isinstance(bad_op, dict):
        swap = {"__A__": a, "__B__": b}
        bad_op = {k: swap.get(v, v) for k, v in bad_op.items()}

    result = await _relate(
        server,
        edges=[{"source_id": a, "target_id": c, "relation_type": "caused_by"}, bad_op],
    )
    assert result["ok"] is False
    assert "edges[1]" in result["error"]
    assert fragment in result["error"]
    assert await _edge_count(server) == 0


@pytest.mark.asyncio
async def test_a_write_failure_midway_rolls_back_the_earlier_edges(server, monkeypatch) -> None:
    """Validation cannot catch everything (a disk error, a lock). If the second
    write fails, the first must not survive: one transaction, not N commits."""
    from gingugu.relations import RelationManager

    a, b, c, _ = await _four(server)
    real_relate = RelationManager.relate
    calls = {"n": 0}

    def flaky(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated write failure")
        return real_relate(self, **kwargs)

    monkeypatch.setattr(RelationManager, "relate", flaky)
    result = await _relate(
        server,
        edges=[
            {"source_id": a, "target_id": b, "relation_type": "supersedes"},
            {"source_id": a, "target_id": c, "relation_type": "caused_by"},
        ],
    )
    monkeypatch.setattr(RelationManager, "relate", real_relate)

    assert result["ok"] is False
    assert await _edge_count(server) == 0


@pytest.mark.asyncio
async def test_empty_batch_is_rejected(server) -> None:
    result = await _relate(server, edges=[])
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_batch_is_capped_at_the_shared_limit(server) -> None:
    from gingugu.handlers.relations import MAX_BATCH_EDGES

    ops = [
        {"source_id": f"a{i}", "target_id": f"b{i}", "relation_type": "related_to"}
        for i in range(MAX_BATCH_EDGES + 1)
    ]
    result = await _relate(server, edges=ops)
    assert result["ok"] is False
    assert str(MAX_BATCH_EDGES) in result["error"]


@pytest.mark.asyncio
async def test_batch_and_single_edge_fields_are_mutually_exclusive(server) -> None:
    a, b, _, _ = await _four(server)
    result = await _relate(
        server,
        source_id=a,
        target_id=b,
        relation_type="caused_by",
        edges=[{"source_id": a, "target_id": b, "relation_type": "caused_by"}],
    )
    assert result["ok"] is False
    assert await _edge_count(server) == 0


@pytest.mark.asyncio
async def test_single_edge_call_without_its_fields_is_rejected(server) -> None:
    """The single-edge fields became optional to make room for ``edges``; a call
    with neither must fail loudly, not report a successful nothing."""
    result = await _relate(server)
    assert result["ok"] is False
