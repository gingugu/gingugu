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


def test_count_dormant_counts_aged_without_mutating(
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

    assert stats.count_dormant(db.conn) == 2
    # Dormancy is a signal only — confidence is NEVER auto-changed.
    assert store.get(aged_v.id, record_access=False).confidence == Confidence.VERIFIED
    assert store.get(aged_i.id, record_access=False).confidence == Confidence.INFERRED
    assert store.get(fresh.id, record_access=False).confidence == Confidence.VERIFIED


def test_count_dormant_excludes_deprecated(
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
    _backdate(db, dep.id, 300)
    assert stats.count_dormant(db.conn) == 0


def test_count_dormant_namespace_scoped(
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

    assert stats.count_dormant(db.conn, namespace_id=ns_a) == 1
    # Still no mutation, even when counted.
    assert store.get(a.id, record_access=False).confidence == Confidence.VERIFIED
    assert store.get(b.id, record_access=False).confidence == Confidence.VERIFIED


def test_stats_reports_dormant_count(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    aged = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="a", content="x")
    _backdate(db, aged.id, 200)
    result = stats.compute_stats(db.conn)
    assert result["dormant_count"] == 1
    # Back-compat alias preserved.
    assert result["stale_count"] == 1


def test_hygiene_flags_ghost_namespaces(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_a = namespaces.get_or_create("has-content").id
    namespaces.get_or_create("empty-one")
    namespaces.get_or_create("empty-two")
    store.create(namespace_id=ns_a, type=MemoryType.FACT, title="t", content="x")
    result = stats.compute_stats(store.conn)
    assert set(result["hygiene"]["ghost_namespaces"]) >= {"empty-one", "empty-two"}
    assert "has-content" not in result["hygiene"]["ghost_namespaces"]


def test_hygiene_flags_duplicate_titles(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="dup", content="x")
    b = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="dup", content="y")
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="solo", content="z")
    result = stats.compute_stats(store.conn)
    hygiene = result["hygiene"]
    assert hygiene["duplicate_title_count"] == 1
    sample = hygiene["duplicate_title_sample"]
    assert len(sample) == 1
    assert sample[0]["title"] == "dup"
    assert sample[0]["namespace"] == "test-ns"
    assert set(sample[0]["ids"]) == {a.id, b.id}


def test_hygiene_ignores_deprecated_dupes(store: MemoryStore, namespaces: NamespaceManager) -> None:
    """Deprecated memories are historical records — duplicate titles across
    one active + one deprecated entry shouldn't trip the cleanup signal."""
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="x")
    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="t",
        content="y",
        confidence=Confidence.DEPRECATED,
    )
    result = stats.compute_stats(store.conn)
    assert result["hygiene"]["duplicate_title_count"] == 0


def test_hygiene_namespace_scoped_skips_ghost_list(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """A namespace-scoped stats call shouldn't surface unrelated empty namespaces."""
    ns_a = namespaces.get_or_create("ns-a").id
    namespaces.get_or_create("empty")
    store.create(namespace_id=ns_a, type=MemoryType.FACT, title="t", content="x")
    result = stats.compute_stats(store.conn, namespace_id=ns_a)
    assert result["hygiene"]["ghost_namespaces"] == []
