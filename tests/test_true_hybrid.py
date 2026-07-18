"""Tests for true hybrid retrieval: independent BM25 + semantic pools, RRF-fused.

The defining behavior: a memory that shares NO keywords with the query
must still surface when its embedding is close to the query's — the
semantic pool is independent of the BM25 candidate pool, not a reranker
gated by it.
"""

from __future__ import annotations

from gingugu import search as search_mod
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


class KeywordEmbedder:
    """Deterministic 4-dim embedder: counts alpha/beta/gamma/delta occurrences."""

    model_name = "fake-test-model"
    dim = 4
    enabled = True

    _KEYS = ("alpha", "beta", "gamma", "delta")

    def encode(self, text: str) -> list[float]:
        low = text.lower()
        return [float(low.count(k)) for k in self._KEYS]

    def encode_many(self, texts):
        return [self.encode(t) for t in texts]


def _embedding_store(conn) -> MemoryStore:
    return MemoryStore(conn, embedder=KeywordEmbedder())


def test_semantic_only_match_surfaces_without_keyword_overlap(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # The semantic pool must be independent: this memory shares zero query
    # keywords, so BM25 alone can never return it.
    estore = _embedding_store(store.conn)
    ns_id = namespaces.get_or_create("test-ns").id
    estore.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="alpha topic",
        content="alpha alpha alpha",
    )
    results = search_mod.search(
        store.conn,
        query="alpha",  # embeds near the alpha memory
        namespace_id=ns_id,
        embedder=KeywordEmbedder(),
    )
    assert len(results) == 1

    # Now a query with NO textual overlap with the stored memory, but whose
    # embedding matches it (both are pure-alpha vectors).
    semantic_only = search_mod.search(
        store.conn,
        query="unrelatedword alpha",
        namespace_id=ns_id,
        embedder=KeywordEmbedder(),
    )
    assert any(m.title == "alpha topic" for m in semantic_only)

    # The hard case: replace the memory text so it shares no tokens at all
    # with the query, keep its alpha embedding via a crafted content.
    estore.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="pure semantic",
        content="beta beta beta beta",
    )
    beta_hit = search_mod.search(
        store.conn,
        query="zzznomatch beta",
        namespace_id=ns_id,
        embedder=KeywordEmbedder(),
    )
    assert any(m.title == "pure semantic" for m in beta_hit)


def test_semantic_pool_respects_filters(store: MemoryStore, namespaces: NamespaceManager) -> None:
    estore = _embedding_store(store.conn)
    ns_a = namespaces.get_or_create("ns-a").id
    ns_b = namespaces.get_or_create("ns-b").id
    estore.create(namespace_id=ns_a, type=MemoryType.FACT, title="gamma a", content="gamma gamma")
    estore.create(namespace_id=ns_b, type=MemoryType.FACT, title="gamma b", content="gamma gamma")
    results = search_mod.search(
        store.conn,
        query="gamma",
        namespace_id=ns_a,
        embedder=KeywordEmbedder(),
    )
    assert [m.title for m in results] == ["gamma a"]


def test_bm25_only_path_unchanged_without_embedder(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="delta note", content="delta findable"
    )
    results = search_mod.search(store.conn, query="findable", namespace_id=ns_id, embedder=None)
    assert len(results) == 1
    # No embedder → a semantically-close but textually-disjoint query finds nothing.
    assert search_mod.search(store.conn, query="zzznomatch", namespace_id=ns_id) == []


def test_match_in_both_pools_outranks_single_pool_match(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    estore = _embedding_store(store.conn)
    ns_id = namespaces.get_or_create("test-ns").id
    estore.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="both pools",
        content="delta report delta delta",
    )
    estore.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="keyword only",
        content="report report alpha alpha alpha",
    )
    results = search_mod.search(
        store.conn,
        query="delta report",
        namespace_id=ns_id,
        embedder=KeywordEmbedder(),
    )
    assert results[0].title == "both pools"
