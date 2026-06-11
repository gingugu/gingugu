"""Tests for memory CRUD operations."""

from __future__ import annotations

from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


def _ns_id(namespaces: NamespaceManager) -> str:
    return namespaces.get_or_create("test-ns").id


def test_create_and_get(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = _ns_id(namespaces)
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.PATTERN,
        title="Async patterns",
        content="Use asyncio.gather for concurrency.",
    )
    fetched = store.get(mem.id, record_access=False)
    assert fetched is not None
    assert fetched.title == "Async patterns"
    assert fetched.confidence == Confidence.INFERRED
    assert fetched.last_confirmed is None


def test_verified_sets_last_confirmed(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = _ns_id(namespaces)
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="t",
        content="c",
        confidence=Confidence.VERIFIED,
    )
    assert mem.last_confirmed is not None


def test_access_increments_count(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = _ns_id(namespaces)
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c")
    store.get(mem.id, record_access=True)
    store.get(mem.id, record_access=True)
    fetched = store.get(mem.id, record_access=False)
    assert fetched is not None
    assert fetched.access_count == 2


def test_update_changes_fields(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = _ns_id(namespaces)
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="old", content="c")
    updated = store.update(mem.id, title="new", confidence=Confidence.VERIFIED)
    assert updated is not None
    assert updated.title == "new"
    assert updated.confidence == Confidence.VERIFIED
    assert updated.last_confirmed is not None


def test_update_metadata_set_keep_clear(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = _ns_id(namespaces)
    mem = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c", metadata='{"k": 1}'
    )
    # None leaves metadata untouched.
    kept = store.update(mem.id, title="t2")
    assert kept is not None and kept.metadata == '{"k": 1}'
    # Empty string clears it to NULL.
    cleared = store.update(mem.id, metadata="")
    assert cleared is not None and cleared.metadata is None


def test_delete(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = _ns_id(namespaces)
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c")
    assert store.delete(mem.id) is True
    assert store.get(mem.id, record_access=False) is None
    assert store.delete(mem.id) is False


def test_get_missing_returns_none(store: MemoryStore) -> None:
    assert store.get("nope", record_access=False) is None
