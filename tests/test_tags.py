"""Tests for tag CRUD, normalization, and tag-filtered search."""

from __future__ import annotations

from gingugu import search as search_mod
from gingugu.models import MemoryType, normalize_tag
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore

WEIGHTS = {"relevance": 0.45, "freshness": 0.25, "access": 0.10, "confidence": 0.20}


def test_normalize_tag() -> None:
    assert normalize_tag("  Python Async ") == "python-async"
    assert normalize_tag("FTS5") == "fts5"
    assert normalize_tag("multi   word\ttag") == "multi-word-tag"


def test_create_with_tags_roundtrip(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="t",
        content="c",
        tags=["Python Async", "sqlite"],
    )
    assert mem.tags == ["python-async", "sqlite"]
    assert store.get(mem.id, record_access=False).tags == ["python-async", "sqlite"]


def test_set_tags_replaces(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c", tags=["a", "b"]
    )
    store.set_tags(mem.id, ["c"])
    assert store.get_tags(mem.id) == ["c"]


def test_add_tags_appends_and_dedupes(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c", tags=["a"])
    result = store.add_tags(mem.id, ["a", "b"])
    assert set(result) == {"a", "b"}


def test_tags_shared_across_memories(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="m1", content="x", tags=["shared"])
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="m2", content="y", tags=["shared"])
    rows = store.conn.execute("SELECT COUNT(*) FROM tags WHERE name = 'shared'").fetchone()
    assert rows[0] == 1  # tag row reused, not duplicated


def test_orphan_tags_pruned_on_set_tags(store: MemoryStore, namespaces: NamespaceManager) -> None:
    # Regression: replaced/removed tags used to linger in the tags table forever.
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c", tags=["old"]
    )
    store.set_tags(mem.id, ["new"])
    names = {r["name"] for r in store.conn.execute("SELECT name FROM tags").fetchall()}
    assert names == {"new"}


def test_orphan_tags_pruned_on_delete(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c", tags=["solo"]
    )
    keeper = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="k", content="c", tags=["kept"]
    )
    store.delete(mem.id)
    names = {r["name"] for r in store.conn.execute("SELECT name FROM tags").fetchall()}
    assert names == {"kept"}
    assert store.get_tags(keeper.id) == ["kept"]


def test_search_tag_filter_requires_all(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="both",
        content="alpha beta",
        tags=["x", "y"],
    )
    store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="onlyx", content="alpha beta", tags=["x"]
    )
    results = search_mod.search(
        store.conn, query="alpha", namespace_id=ns_id, weights=WEIGHTS, tags=["x", "y"]
    )
    titles = {m.title for m in results}
    assert titles == {"both"}


def test_advanced_search_tag_filter_no_query(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="tagged", content="c", tags=["keep"]
    )
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="untagged", content="c")
    results = search_mod.advanced_search(
        store.conn, namespace_id=ns_id, weights=WEIGHTS, tags=["keep"], sort_by="created"
    )
    assert {m.title for m in results} == {"tagged"}
