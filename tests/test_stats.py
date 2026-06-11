"""Tests for memory_stats and access-log pruning."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from gingugu import stats
from gingugu.database import Database
from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


def test_stats_counts_by_type_and_confidence(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="a", content="x")
    store.create(
        namespace_id=ns_id,
        type=MemoryType.BUG,
        title="b",
        content="y",
        confidence=Confidence.VERIFIED,
    )
    result = stats.compute_stats(store.conn)
    assert result["total_memories"] == 2
    assert result["by_type"]["fact"] == 1
    assert result["by_type"]["bug"] == 1
    assert result["by_confidence"]["verified"] == 1


def test_stats_namespace_breakdown(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_a = namespaces.get_or_create("ns-a").id
    namespaces.get_or_create("ns-b")
    store.create(namespace_id=ns_a, type=MemoryType.FACT, title="a", content="x")
    result = stats.compute_stats(store.conn)
    names = {n["name"]: n["count"] for n in result["namespaces"]}
    assert names["ns-a"] == 1
    assert names["ns-b"] == 0


def test_stats_credential_health(store: MemoryStore) -> None:
    result = stats.compute_stats(store.conn)
    assert result["credentials"]["total"] == 0


def test_prune_access_log_removes_old_rows(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="a", content="x")
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    db.conn.execute(
        "INSERT INTO access_log(id, memory_id, accessed_at) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), mem.id, old),
    )
    db.conn.commit()
    before = db.conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0]
    deleted = stats.prune_access_log(db.conn, force=True)
    after = db.conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0]
    assert deleted >= 1
    assert after < before


def test_prune_is_throttled(db: Database) -> None:
    stats.prune_access_log(db.conn, force=True)
    # Immediate second call without force should be throttled to a no-op.
    assert stats.prune_access_log(db.conn, force=False) == 0


def _backdate(db: Database, memory_id: str, days: int) -> None:
    old = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    db.conn.execute("UPDATE memories SET last_accessed = ? WHERE id = ?", (old, memory_id))
    db.conn.commit()


def test_flag_stale_demotes_aged_active(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    aged_v = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="av",
        content="x",
        confidence=Confidence.VERIFIED,
    )
    aged_i = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="ai",
        content="y",
        confidence=Confidence.INFERRED,
    )
    fresh = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="f",
        content="z",
        confidence=Confidence.VERIFIED,
    )
    _backdate(db, aged_v.id, 200)
    _backdate(db, aged_i.id, 100)

    flagged = stats.flag_stale(db.conn)

    assert flagged == 2
    assert store.get(aged_v.id, record_access=False).confidence == Confidence.STALE
    assert store.get(aged_i.id, record_access=False).confidence == Confidence.STALE
    assert store.get(fresh.id, record_access=False).confidence == Confidence.VERIFIED


def test_flag_stale_skips_deprecated_and_stale(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    dep = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="d",
        content="x",
        confidence=Confidence.DEPRECATED,
    )
    already = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="s",
        content="y",
        confidence=Confidence.STALE,
    )
    _backdate(db, dep.id, 300)
    _backdate(db, already.id, 300)

    assert stats.flag_stale(db.conn) == 0
    assert store.get(dep.id, record_access=False).confidence == Confidence.DEPRECATED


def test_flag_stale_namespace_scoped(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_a = namespaces.get_or_create("ns-a").id
    ns_b = namespaces.get_or_create("ns-b").id
    a = store.create(
        namespace_id=ns_a,
        type=MemoryType.FACT,
        title="a",
        content="x",
        confidence=Confidence.VERIFIED,
    )
    b = store.create(
        namespace_id=ns_b,
        type=MemoryType.FACT,
        title="b",
        content="y",
        confidence=Confidence.VERIFIED,
    )
    _backdate(db, a.id, 200)
    _backdate(db, b.id, 200)

    assert stats.flag_stale(db.conn, namespace_id=ns_a) == 1
    assert store.get(a.id, record_access=False).confidence == Confidence.STALE
    assert store.get(b.id, record_access=False).confidence == Confidence.VERIFIED


def test_flag_stale_is_reversible(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="m",
        content="x",
        confidence=Confidence.VERIFIED,
    )
    _backdate(db, mem.id, 200)
    stats.flag_stale(db.conn)
    restored = store.update(mem.id, confidence=Confidence.VERIFIED)
    assert restored.confidence == Confidence.VERIFIED
    assert restored.content == "x"
