"""Tests for memory CRUD operations."""

from __future__ import annotations

import pytest

from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore, _normalize_metadata


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


def test_record_accesses_bulk(store: MemoryStore, namespaces: NamespaceManager) -> None:
    """Bulk record_accesses bumps access_count for every id and writes one
    access_log row per id. De-duplicates and ignores empty ids."""
    ns_id = _ns_id(namespaces)
    a = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="a", content="c")
    b = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="b", content="c")

    written = store.record_accesses([a.id, b.id, a.id, "", None])  # type: ignore[list-item]
    assert written == 2  # de-duped, empties stripped

    fa = store.get(a.id, record_access=False)
    fb = store.get(b.id, record_access=False)
    assert fa is not None and fa.access_count == 1
    assert fb is not None and fb.access_count == 1

    log_rows = store.conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0]
    assert log_rows == 2


def test_record_accesses_empty_is_noop(store: MemoryStore) -> None:
    assert store.record_accesses([]) == 0
    assert store.conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0] == 0


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


# --- metadata JSON validation ---------------------------------------------


def test_normalize_metadata_none_passthrough() -> None:
    assert _normalize_metadata(None) is None


def test_normalize_metadata_empty_string_clears() -> None:
    assert _normalize_metadata("") is None


def test_normalize_metadata_canonicalizes_keys() -> None:
    """Equivalent JSON objects should produce identical stored strings."""
    a = _normalize_metadata('{"b": 1, "a": 2}')
    b = _normalize_metadata('{"a": 2, "b": 1}')
    assert a == b == '{"a": 2, "b": 1}'


def test_normalize_metadata_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="must be valid JSON"):
        _normalize_metadata("not json at all")


def test_normalize_metadata_rejects_non_object_shapes() -> None:
    """Lists, scalars, etc. are valid JSON but not what metadata should hold."""
    for payload in ("[1, 2, 3]", '"a string"', "42", "true", "null"):
        with pytest.raises(ValueError, match="JSON object"):
            _normalize_metadata(payload)


def test_create_rejects_bad_metadata(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = _ns_id(namespaces)
    with pytest.raises(ValueError):
        store.create(
            namespace_id=ns_id,
            type=MemoryType.FACT,
            title="t",
            content="c",
            metadata="not json",
        )


def test_update_rejects_bad_metadata(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = _ns_id(namespaces)
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c")
    with pytest.raises(ValueError):
        store.update(mem.id, metadata="[1, 2, 3]")


def test_create_canonicalizes_metadata_on_store(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """Stored metadata should come back in canonical (sorted-keys) form."""
    ns_id = _ns_id(namespaces)
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="t",
        content="c",
        metadata='{"z": 1, "a": 2}',
    )
    assert mem.metadata == '{"a": 2, "z": 1}'
