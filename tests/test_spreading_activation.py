"""Tests for spreading activation: recall reactivates related memories.

A robot brain never forgets. When a memory is recalled, its relation neighbours
have their dormancy clock reset (``last_accessed`` refreshed) without inflating
their access counts — the core of the never-forget model.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gingugu.config import Config
from gingugu.database import Database
from gingugu.handlers import ServerContext
from gingugu.handlers.helpers import _spread_activation
from gingugu.models import MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore


def _backdate(db: Database, memory_id: str, days: int) -> None:
    old = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    db.conn.execute("UPDATE memories SET last_accessed = ? WHERE id = ?", (old, memory_id))
    db.conn.commit()


def _mem(store: MemoryStore, ns_id: str, title: str) -> str:
    return store.create(namespace_id=ns_id, type=MemoryType.FACT, title=title, content="c").id


def test_touch_many_refreshes_without_counting_access(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = _mem(store, ns_id, "a")
    _backdate(db, a, 200)
    before = store.get(a, record_access=False)
    assert before.access_count == 0

    touched = store.touch_many([a])

    after = store.get(a, record_access=False)
    assert touched == 1
    # last_accessed reset to ~now (no longer dormant)...
    assert after.last_accessed > before.last_accessed
    # ...but this is a reactivation, not a real access.
    assert after.access_count == 0
    log_rows = db.conn.execute(
        "SELECT COUNT(*) FROM access_log WHERE memory_id = ?", (a,)
    ).fetchone()[0]
    assert log_rows == 0


def test_touch_many_dedups_and_ignores_empty(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = _mem(store, ns_id, "a")
    assert store.touch_many([]) == 0
    assert store.touch_many(["", None]) == 0  # type: ignore[list-item]
    assert store.touch_many([a, a, a]) == 1


def _ctx(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config
) -> ServerContext:
    return ServerContext(config=config, store=store, namespaces=namespaces, conn=db.conn)


def test_spread_activation_wakes_related_neighbour(
    db: Database,
    store: MemoryStore,
    namespaces: NamespaceManager,
    relations: RelationManager,
    config: Config,
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    seed = _mem(store, ns_id, "seed")
    neighbour = _mem(store, ns_id, "neighbour")
    relations.relate(source_id=seed, target_id=neighbour, relation_type=RelationType.RELATED_TO)
    _backdate(db, neighbour, 200)
    dormant_before = store.get(neighbour, record_access=False).last_accessed

    ctx = _ctx(db, store, namespaces, config)
    woken = _spread_activation(ctx, [seed])

    after = store.get(neighbour, record_access=False)
    assert woken == 1
    assert after.last_accessed > dormant_before
    assert after.access_count == 0  # reactivation, not access


def test_spread_activation_skips_seeds_and_handles_no_relations(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    lonely = _mem(store, ns_id, "lonely")
    ctx = _ctx(db, store, namespaces, config)
    # No relations -> nothing to wake; a seed never reactivates itself.
    assert _spread_activation(ctx, [lonely]) == 0
    assert _spread_activation(ctx, []) == 0
