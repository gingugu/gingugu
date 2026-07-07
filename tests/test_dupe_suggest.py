"""Tests for memory_consolidate suggest mode (proactive near-dupe surfacing)."""

from __future__ import annotations

import json

import pytest

from gingugu import embeddings as emb
from gingugu.consolidation import find_duplicate_clusters, find_title_duplicate_clusters
from gingugu.models import MemoryType, utcnow_iso

# --- semantic scan (unit level, hand-stamped embeddings) ---------------------


def _stamp_embedding(conn, memory_id: str, vec: list[float]) -> None:
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO memory_embeddings(memory_id, model, dim, embedding, created_at, updated_at) "
        "VALUES (?, 'test-model', ?, ?, ?, ?)",
        (memory_id, len(vec), emb.pack(vec), now, now),
    )
    conn.commit()


@pytest.fixture
def seeded(db, store, namespaces):
    ns = namespaces.get_or_create("dupes-ns")
    a = store.create(namespace_id=ns.id, type=MemoryType.PATTERN, title="wal a", content="a")
    b = store.create(namespace_id=ns.id, type=MemoryType.PATTERN, title="wal b", content="b")
    c = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="other", content="c")
    _stamp_embedding(db.conn, a.id, [1.0, 0.0])
    _stamp_embedding(db.conn, b.id, [0.98, 0.2])  # cosine vs a ≈ 0.98
    _stamp_embedding(db.conn, c.id, [0.0, 1.0])  # orthogonal to both
    return db.conn, ns, a, b, c


def test_semantic_scan_clusters_near_dupes(seeded) -> None:
    conn, ns, a, b, c = seeded
    result = find_duplicate_clusters(conn, namespace_id=ns.id, min_similarity=0.85)
    assert result["mode"] == "semantic"
    assert result["scanned"] == 3
    assert len(result["clusters"]) == 1
    cluster = result["clusters"][0]
    assert set(cluster["ids"]) == {a.id, b.id}
    assert cluster["similarity"] >= 0.95
    assert set(cluster["titles"]) == {"wal a", "wal b"}


def test_semantic_scan_respects_threshold(seeded) -> None:
    conn, ns, *_ = seeded
    result = find_duplicate_clusters(conn, namespace_id=ns.id, min_similarity=0.999)
    assert result["clusters"] == []


def test_semantic_scan_skips_dim_mismatch_and_missing(seeded) -> None:
    conn, ns, a, b, c = seeded
    from gingugu.storage import MemoryStore

    store = MemoryStore(conn)
    d = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="threedim", content="d")
    _stamp_embedding(conn, d.id, [1.0, 0.0, 0.0])  # different embedding dim
    store.create(namespace_id=ns.id, type=MemoryType.FACT, title="noembed", content="e")

    result = find_duplicate_clusters(conn, namespace_id=ns.id)
    assert result["skipped_no_embedding"] == 1  # e
    assert all(d.id not in cl["ids"] for cl in result["clusters"])  # no cross-dim pair


def test_deprecated_memories_are_excluded(seeded) -> None:
    conn, ns, a, b, c = seeded
    from gingugu.models import Confidence
    from gingugu.storage import MemoryStore

    MemoryStore(conn).update(b.id, confidence=Confidence.DEPRECATED)
    result = find_duplicate_clusters(conn, namespace_id=ns.id)
    assert result["clusters"] == []  # a lost its partner


def test_title_fallback_clusters_exact_titles(db, store, namespaces) -> None:
    ns = namespaces.get_or_create("title-ns")
    x = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="same", content="1")
    y = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="same", content="2")
    store.create(namespace_id=ns.id, type=MemoryType.FACT, title="unique", content="3")
    result = find_title_duplicate_clusters(db.conn, namespace_id=ns.id)
    assert result["mode"] == "title-only"
    assert len(result["clusters"]) == 1
    assert set(result["clusters"][0]["ids"]) == {x.id, y.id}


# --- tool surface -------------------------------------------------------------


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "suggest.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "suggest-ns")
    # Force the Null provider so this test is deterministic on machines with a
    # cached fastembed model (no embedding rows → title-only fallback path).
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


@pytest.mark.asyncio
async def test_suggest_mode_then_consolidate_round_trip(server) -> None:
    """Omitting memory_ids returns candidate clusters (read-only); feeding a
    cluster back with a strategy performs the actual consolidation."""
    one = _payload(
        await server.call_tool(
            "memory_store",
            {"content": "use WAL mode", "title": "wal rule", "type": "pattern"},
        )
    )
    two = _payload(
        await server.call_tool(
            "memory_store",
            {"content": "use WAL mode always", "title": "wal rule", "type": "pattern"},
        )
    )
    assert one["ok"] and two["ok"]

    suggest = _payload(await server.call_tool("memory_consolidate", {}))
    assert suggest["ok"]
    assert suggest["namespace"] == "suggest-ns"
    assert suggest["mode"] == "title-only"  # embeddings disabled in this fixture
    assert len(suggest["clusters"]) == 1
    ids = suggest["clusters"][0]["ids"]
    assert set(ids) == {one["memory"]["id"], two["memory"]["id"]}

    merged = _payload(
        await server.call_tool(
            "memory_consolidate",
            {"memory_ids": ",".join(ids), "strategy": "deduplicate"},
        )
    )
    assert merged["ok"]
    assert merged["strategy"] == "deduplicate"

    # After consolidation the dupes are deprecated — a re-scan comes back clean.
    rescan = _payload(await server.call_tool("memory_consolidate", {}))
    assert rescan["ok"] and rescan["clusters"] == []


@pytest.mark.asyncio
async def test_suggest_mode_unknown_namespace_errors(server) -> None:
    result = _payload(await server.call_tool("memory_consolidate", {"namespace": "nope-ns"}))
    assert result["ok"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_suggest_mode_validates_min_similarity(server) -> None:
    result = _payload(await server.call_tool("memory_consolidate", {"min_similarity": 1.5}))
    assert result["ok"] is False
