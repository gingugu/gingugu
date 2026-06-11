"""Tests for FTS5 search + BM25 recall."""

from __future__ import annotations

from gingugu import search as search_mod
from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.search import build_match_query
from gingugu.storage import MemoryStore


def _seed(store: MemoryStore, ns_id: str) -> None:
    store.create(
        namespace_id=ns_id,
        type=MemoryType.PATTERN,
        title="Python async patterns",
        content="Use asyncio.gather to run coroutines concurrently.",
    )
    store.create(
        namespace_id=ns_id,
        type=MemoryType.BUG,
        title="SQLite locking",
        content="WAL mode allows concurrent readers with a single writer.",
    )


def test_build_match_query_quotes_tokens() -> None:
    # Tokens are quoted (literals) and joined with OR for recall-first matching.
    assert build_match_query("hello world") == '"hello" OR "world"'
    assert build_match_query("  ") is None
    # A literal "OR" token stays quoted, so it can't act as an FTS operator.
    assert build_match_query("a OR b") == '"a" OR "OR" OR "b"'


def test_search_natural_language_query_ignores_absent_words(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # Regression: a full-sentence query must not return nothing just because it
    # contains words absent from the corpus (the old implicit-AND behavior).
    ns_id = namespaces.get_or_create("test-ns").id
    _seed(store, ns_id)
    results = search_mod.search(
        store.conn,
        query="when did we decide to use asyncio for coroutines",
        namespace_id=ns_id,
    )
    assert len(results) >= 1
    assert "async" in results[0].title.lower()


def test_search_finds_by_term(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    _seed(store, ns_id)
    results = search_mod.search(store.conn, query="asyncio", namespace_id=ns_id)
    assert len(results) == 1
    assert "async" in results[0].title.lower()
    assert results[0].score is not None and 0.0 <= results[0].score <= 1.0


def test_search_porter_stemming(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    _seed(store, ns_id)
    results = search_mod.search(store.conn, query="concurrent", namespace_id=ns_id)
    assert len(results) == 2


def test_search_namespace_scoping(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_a = namespaces.get_or_create("ns-a").id
    ns_b = namespaces.get_or_create("ns-b").id
    store.create(namespace_id=ns_a, type=MemoryType.FACT, title="alpha", content="findme alpha")
    results_b = search_mod.search(store.conn, query="findme", namespace_id=ns_b)
    assert results_b == []
    results_a = search_mod.search(store.conn, query="findme", namespace_id=ns_a)
    assert len(results_a) == 1


def test_search_excludes_deprecated(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="zombie", content="deprecated zombie"
    )
    store.update(mem.id, confidence=Confidence.DEPRECATED)
    assert search_mod.search(store.conn, query="zombie", namespace_id=ns_id) == []
    incl = search_mod.search(
        store.conn, query="zombie", namespace_id=ns_id, include_deprecated=True
    )
    assert len(incl) == 1


def test_search_min_confidence_filter(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="weak",
        content="signal weak",
        confidence=Confidence.INFERRED,
    )
    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="strong",
        content="signal strong",
        confidence=Confidence.VERIFIED,
    )
    results = search_mod.search(
        store.conn, query="signal", namespace_id=ns_id, min_confidence=Confidence.VERIFIED
    )
    assert len(results) == 1
    assert results[0].title == "strong"


def test_advanced_search_date_filter_applies_before_limit(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # Regression: created_after/before were applied *after* the SQL LIMIT, so a
    # small limit full of out-of-range rows could return nothing.
    ns_id = namespaces.get_or_create("test-ns").id
    old = "2020-01-01T00:00:00+00:00"
    for i in range(10):
        mem = store.create(
            namespace_id=ns_id, type=MemoryType.FACT, title=f"old{i}", content="findme common"
        )
        store.conn.execute(
            "UPDATE memories SET created_at = ?, last_accessed = ? WHERE id = ?",
            (old, "2030-01-01T00:00:00+00:00", mem.id),
        )
    store.conn.commit()
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="recent", content="findme common")

    results = search_mod.advanced_search(
        store.conn,
        query="findme",
        namespace_id=ns_id,
        created_after="2025-01-01",
        limit=1,
    )
    assert [m.title for m in results] == ["recent"]

    # Same guarantee on the no-query listing path.
    listed = search_mod.advanced_search(
        store.conn,
        namespace_id=ns_id,
        created_after="2025-01-01",
        sort_by="created",
        limit=1,
    )
    assert [m.title for m in listed] == ["recent"]


def test_search_min_confidence_in_sql(store: MemoryStore, namespaces: NamespaceManager) -> None:
    # Regression: min_confidence used to filter after the LIMIT; many weak rows
    # could crowd strong ones out of the candidate pool entirely.
    ns_id = namespaces.get_or_create("test-ns").id
    for i in range(10):
        store.create(
            namespace_id=ns_id,
            type=MemoryType.FACT,
            title=f"weak{i}",
            content="signal noise",
            confidence=Confidence.INFERRED,
        )
    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="strong",
        content="signal noise",
        confidence=Confidence.VERIFIED,
    )
    results = search_mod.search(
        store.conn,
        query="signal",
        namespace_id=ns_id,
        min_confidence=Confidence.VERIFIED,
        limit=1,
    )
    assert [m.title for m in results] == ["strong"]


def test_update_reindexes(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="t", content="original gather term"
    )
    store.update(mem.id, content="replaced trio term")
    assert search_mod.search(store.conn, query="gather", namespace_id=ns_id) == []
    assert len(search_mod.search(store.conn, query="trio", namespace_id=ns_id)) == 1
