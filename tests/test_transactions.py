"""Tests for `transactions.atomic` and the all-or-nothing consolidation path."""

from __future__ import annotations

import pytest

from gingugu import consolidation
from gingugu.database import Database
from gingugu.models import Confidence, MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore
from gingugu.transactions import atomic


class Boom(Exception):
    """Raised mid-block to force a rollback."""


def _seed(store: MemoryStore, ns_id: str, n: int = 3) -> list[str]:
    return [
        store.create(
            namespace_id=ns_id,
            type=MemoryType.FACT,
            title=f"T{i}",
            content=f"body {i}",
            tags=[f"tag{i}"],
        ).id
        for i in range(n)
    ]


def _count(store: MemoryStore) -> int:
    return store.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


# --------------------------------------------------------------------------
# atomic() mechanics
# --------------------------------------------------------------------------


def test_atomic_commits_once_on_success(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    with atomic(store, relations):
        first = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="a", content="one")
        second = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="b", content="two")
        relations.relate(
            source_id=first.id, target_id=second.id, relation_type=RelationType.SUPERSEDES
        )
    assert store.get(first.id, record_access=False) is not None
    assert relations.related_ids(first.id) == [second.id]


def test_atomic_rolls_back_every_write(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    before = _count(store)
    with pytest.raises(Boom):
        with atomic(store, relations):
            store.create(namespace_id=ns_id, type=MemoryType.FACT, title="a", content="one")
            store.create(namespace_id=ns_id, type=MemoryType.FACT, title="b", content="two")
            raise Boom
    assert _count(store) == before


def test_atomic_rollback_undoes_deletes(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """The path that actually loses data: a hard delete inside a failed block."""
    ns_id = namespaces.get_or_create("test-ns").id
    ids = _seed(store, ns_id)
    with pytest.raises(Boom):
        with atomic(store, relations):
            store.delete(ids[0])
            store.delete(ids[1])
            raise Boom
    for mid in ids:
        assert store.get(mid, record_access=False) is not None


def test_gate_reopens_after_block(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    with pytest.raises(Boom):
        with atomic(store, relations):
            raise Boom
    assert store._suppress_commit is False
    assert relations._suppress_commit is False
    # And an ordinary write still commits on its own afterwards.
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="a", content="one")
    assert store.conn.in_transaction is False
    assert store.get(mem.id, record_access=False) is not None


def test_atomic_rejects_nesting(store: MemoryStore, relations: RelationManager) -> None:
    with atomic(store, relations):
        with pytest.raises(RuntimeError, match="do not nest"):
            with atomic(store):
                pass


def test_atomic_rejects_foreign_connection(store: MemoryStore, tmp_path) -> None:
    other = Database(tmp_path / "other.db")
    other.connect()
    try:
        with pytest.raises(ValueError, match="share one connection"):
            with atomic(store, MemoryStore(other.conn)):
                pass
    finally:
        other.close()


def test_atomic_requires_a_participant() -> None:
    with pytest.raises(ValueError, match="at least one participant"):
        with atomic():
            pass


# --------------------------------------------------------------------------
# Deferred post-commit side effects
# --------------------------------------------------------------------------


def test_deferred_effect_runs_after_commit(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    ran: list[str] = []
    with atomic(store, relations):
        store.create(namespace_id=ns_id, type=MemoryType.FACT, title="a", content="one")
        store._after_commit(lambda: ran.append("inside"))
        assert ran == []  # queued, not yet run
    assert ran == ["inside"]


def test_deferred_effect_discarded_on_rollback(
    store: MemoryStore, relations: RelationManager
) -> None:
    ran: list[str] = []
    with pytest.raises(Boom):
        with atomic(store, relations):
            store._after_commit(lambda: ran.append("inside"))
            raise Boom
    assert ran == []
    assert store._deferred == []


def test_failing_deferred_effect_does_not_undo_the_commit(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """Embeddings are best-effort: an encode failure must not fail the write."""
    ns_id = namespaces.get_or_create("test-ns").id

    def explode() -> None:
        raise Boom

    with atomic(store, relations):
        mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="a", content="one")
        store._after_commit(explode)
    assert store.get(mem.id, record_access=False) is not None


# --------------------------------------------------------------------------
# consolidate() is all-or-nothing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", ["merge", "summarize", "deduplicate"])
def test_consolidate_rolls_back_when_retirement_fails(
    store: MemoryStore,
    namespaces: NamespaceManager,
    relations: RelationManager,
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    """A failure partway through the retirement loop leaves nothing behind.

    This is the board-item-1 defect: `1 + 2N` unguarded commits meant the new
    memory and the already-retired originals stayed committed while the rest
    did not.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    ids = _seed(store, ns_id)
    before = _count(store)

    calls = {"n": 0}
    real_update = store.update

    def flaky_update(memory_id: str, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise Boom
        return real_update(memory_id, **kwargs)

    monkeypatch.setattr(store, "update", flaky_update)

    with pytest.raises(Boom):
        consolidation.consolidate(store, relations, memory_ids=ids, strategy=strategy)

    assert _count(store) == before
    for mid in ids:
        mem = store.get(mid, record_access=False)
        assert mem is not None
        assert mem.confidence != Confidence.DEPRECATED


@pytest.mark.parametrize("strategy", ["merge", "summarize", "deduplicate"])
def test_consolidate_rollback_preserves_originals_on_hard_delete(
    store: MemoryStore,
    namespaces: NamespaceManager,
    relations: RelationManager,
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    """`keep_originals=False` is the unrecoverable path - prove nothing is lost."""
    ns_id = namespaces.get_or_create("test-ns").id
    ids = _seed(store, ns_id)
    before = _count(store)

    calls = {"n": 0}
    real_delete = store.delete

    def flaky_delete(memory_id: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 2:
            raise Boom
        return real_delete(memory_id)

    monkeypatch.setattr(store, "delete", flaky_delete)

    with pytest.raises(Boom):
        consolidation.consolidate(
            store, relations, memory_ids=ids, strategy=strategy, keep_originals=False
        )

    assert _count(store) == before
    for mid in ids:
        assert store.get(mid, record_access=False) is not None


def test_deduplicate_folds_tags_before_retiring(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """The union must be applied before a hard delete removes its source rows."""
    ns_id = namespaces.get_or_create("test-ns").id
    ids = _seed(store, ns_id)
    result = consolidation.consolidate(
        store, relations, memory_ids=ids, strategy="deduplicate", keep_originals=False
    )
    kept = store.get(result["consolidated_id"], record_access=False)
    assert set(kept.tags) == {"tag0", "tag1", "tag2"}
