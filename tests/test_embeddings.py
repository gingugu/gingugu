"""Tests for the embeddings module — provider protocol, pack/unpack, cosine,
and the storage/search integration via a deterministic fake provider.

Real fastembed model loads are NOT exercised here — those are expensive and
exercised end-to-end via the integration tests in CI. Everything here uses
a `FakeEmbedder` that returns repeatable, small vectors.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gingugu.database import Database
from gingugu.embeddings import (
    NullEmbeddingProvider,
    OllamaEmbeddingProvider,
    build_provider,
    cosine,
    pack,
    unpack,
)
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.search import _fuse_ranks, search
from gingugu.storage import MemoryStore


class FakeEmbedder:
    """Deterministic 4-dim embedder for tests.

    Vectors encode keyword presence: each text gets a 4-vector counting
    occurrences of the words "alpha", "beta", "gamma", "delta". Different
    enough that cosine sim is predictable; cheap enough to run millions of.
    """

    model_name = "fake-test-model"
    dim = 4
    enabled = True

    _KEYS = ("alpha", "beta", "gamma", "delta")

    def encode(self, text: str) -> list[float]:
        low = text.lower()
        return [float(low.count(k)) for k in self._KEYS]

    def encode_many(self, texts):
        return [self.encode(t) for t in texts]


# --- pack/unpack/cosine primitives -----------------------------------------


def test_pack_roundtrip_preserves_values_to_float32_precision():
    vec = [0.1, -0.5, 1.0, 0.0, 3.14159]
    blob = pack(vec)
    restored = unpack(blob)
    assert len(restored) == len(vec)
    for a, b in zip(vec, restored, strict=False):
        assert abs(a - b) < 1e-6


def test_pack_empty_vector():
    assert pack([]) == b""
    assert unpack(b"") == []


def test_cosine_orthogonal_vectors():
    assert cosine([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)


def test_cosine_identical_vectors():
    assert cosine([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_cosine_opposite_vectors():
    assert cosine([1, 0, 0], [-1, 0, 0]) == pytest.approx(-1.0)


def test_cosine_handles_zero_vectors():
    assert cosine([0, 0, 0], [1, 2, 3]) == 0.0
    assert cosine([1, 2, 3], [0, 0, 0]) == 0.0
    assert cosine([], []) == 0.0


def test_cosine_mismatched_dims_returns_zero():
    assert cosine([1, 0], [1, 0, 0]) == 0.0


# --- NullEmbeddingProvider --------------------------------------------------


def test_null_provider_is_disabled_and_no_op():
    p = NullEmbeddingProvider()
    assert not p.enabled
    assert p.encode("hello") is None
    assert p.encode_many(["a", "b"]) == [None, None]
    assert p.dim == 0
    assert p.model_name == "none"


def test_build_provider_disabled_returns_null():
    p = build_provider(enabled=False)
    assert not p.enabled
    assert p.encode("anything") is None


# --- OllamaEmbeddingProvider ------------------------------------------------


def _mock_ollama_response(vec: list[float]) -> MagicMock:
    """Build a mock urllib response that returns the given embedding vector."""
    body = f'{{"embedding": {vec}}}'
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = body.encode()
    return resp


def test_ollama_provider_encode_success():
    provider = OllamaEmbeddingProvider(model_name="nomic-embed-text", host="http://localhost:11434")
    mock_resp = _mock_ollama_response([0.1, 0.2, 0.3])

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = provider.encode("hello world")

    assert result == pytest.approx([0.1, 0.2, 0.3])


def test_ollama_provider_dim_detected_on_first_call():
    provider = OllamaEmbeddingProvider()
    assert provider.dim == 0

    mock_resp = _mock_ollama_response([0.5, 0.6, 0.7, 0.8])
    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider.encode("probe")

    assert provider.dim == 4


def test_ollama_provider_encode_returns_none_on_failure():
    provider = OllamaEmbeddingProvider()
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = provider.encode("hello")
    assert result is None


def test_ollama_provider_encode_many():
    provider = OllamaEmbeddingProvider()
    mock_resp = _mock_ollama_response([1.0, 0.0])
    with patch("urllib.request.urlopen", return_value=mock_resp):
        results = provider.encode_many(["a", "b", "c"])
    assert len(results) == 3
    assert all(r == pytest.approx([1.0, 0.0]) for r in results)


def test_ollama_provider_strips_trailing_slash_from_host():
    provider = OllamaEmbeddingProvider(host="http://localhost:11434/")
    assert provider._host == "http://localhost:11434"


def test_build_provider_ollama_falls_back_on_unreachable():
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        provider = build_provider(enabled=True, backend="ollama")
    assert not provider.enabled
    assert isinstance(provider, NullEmbeddingProvider)


def test_build_provider_ollama_returns_ollama_provider_on_success():
    mock_resp = _mock_ollama_response([0.1, 0.2])
    with patch("urllib.request.urlopen", return_value=mock_resp):
        provider = build_provider(enabled=True, backend="ollama")
    assert provider.enabled
    assert isinstance(provider, OllamaEmbeddingProvider)


def test_build_provider_ollama_disabled_returns_null():
    provider = build_provider(enabled=False, backend="ollama")
    assert not provider.enabled
    assert isinstance(provider, NullEmbeddingProvider)


# --- RRF fusion -------------------------------------------------------------


def test_fuse_ranks_single_ranking_normalizes_to_one_at_top():
    """Best item in a single ranking should map to 1.0."""
    fused = _fuse_ranks({"a": 1, "b": 2, "c": 3}, None)
    assert fused["a"] == pytest.approx(1.0)
    assert fused["b"] < 1.0
    assert fused["c"] < fused["b"]


def test_fuse_ranks_two_rankings_rewards_overlap():
    """An item that wins both rankings should outscore one that wins only one."""
    bm25 = {"a": 1, "b": 2, "c": 3}
    sem = {"a": 1, "c": 2, "d": 3}
    fused = _fuse_ranks(bm25, sem)
    # 'a' is rank 1 in both → max score = 1.0
    assert fused["a"] == pytest.approx(1.0)
    # 'c' is in both (mid in bm25, second in sem) → beats 'b' (only bm25 rank 2)
    assert fused["c"] > fused["b"]
    # 'd' is only in semantic → still gets a score, but lower than 'a'
    assert 0.0 < fused["d"] < fused["a"]


def test_fuse_ranks_handles_empty_inputs():
    assert _fuse_ranks({}, None) == {}
    assert _fuse_ranks({}, {}) == {}


# --- Storage integration ----------------------------------------------------


def _seed_namespace(ns: NamespaceManager) -> str:
    return ns.get_or_create("test-ns").id


def test_storage_persists_embedding_on_create(db: Database):
    ns = NamespaceManager(db.conn, None)
    ns_id = _seed_namespace(ns)
    store = MemoryStore(db.conn, embedder=FakeEmbedder())

    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="Alpha sighting",
        content="Saw alpha and beta near the dock.",
    )

    row = db.conn.execute(
        "SELECT model, dim FROM memory_embeddings WHERE memory_id = ?", (mem.id,)
    ).fetchone()
    assert row is not None
    assert row["model"] == "fake-test-model"
    assert row["dim"] == 4


def test_storage_reencodes_on_content_change(db: Database):
    ns = NamespaceManager(db.conn, None)
    ns_id = _seed_namespace(ns)
    store = MemoryStore(db.conn, embedder=FakeEmbedder())
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="Original",
        content="alpha alpha",
    )
    original = store.get_embedding(mem.id)

    store.update(mem.id, content="gamma gamma gamma")
    updated = store.get_embedding(mem.id)

    assert original is not None and updated is not None
    assert original != updated


def test_storage_skips_reencoding_on_metadata_only_update(db: Database):
    ns = NamespaceManager(db.conn, None)
    ns_id = _seed_namespace(ns)
    store = MemoryStore(db.conn, embedder=FakeEmbedder())
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="Stable",
        content="alpha beta",
    )
    before = store.get_embedding(mem.id)

    store.update(mem.id, metadata='{"note":"unrelated"}')
    after = store.get_embedding(mem.id)

    assert before == after


def test_null_embedder_writes_no_embedding_rows(db: Database):
    ns = NamespaceManager(db.conn, None)
    ns_id = _seed_namespace(ns)
    store = MemoryStore(db.conn)  # default = NullEmbeddingProvider

    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="No vector",
        content="anything",
    )
    count = db.conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    assert count == 0


def test_backfill_embeddings_writes_missing_rows(db: Database):
    ns = NamespaceManager(db.conn, None)
    ns_id = _seed_namespace(ns)
    # First seed memories without an embedder.
    plain = MemoryStore(db.conn)
    for i in range(3):
        plain.create(
            namespace_id=ns_id,
            type=MemoryType.FACT,
            title=f"row-{i}",
            content=f"alpha {i}",
        )
    assert db.conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0

    # Now backfill via an embedder-enabled store.
    store = MemoryStore(db.conn, embedder=FakeEmbedder())
    written = store.backfill_embeddings(batch_size=10)
    assert written == 3
    assert db.conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 3


# --- Hybrid search ----------------------------------------------------------


def test_hybrid_search_prefers_semantically_similar_when_bm25_ties(db: Database):
    """With FTS5 ranking near-tied, semantic ranking should be the tiebreaker.

    The query mentions 'alpha gamma'. Two memories share the token coverage
    (one literal 'alpha gamma', one literal 'beta delta' that won't match
    FTS at all, and one 'alpha gamma beta' with extra tokens). The fake
    embedder makes the alpha-gamma-heavy memory semantically closest.
    """
    ns = NamespaceManager(db.conn, None)
    ns_id = _seed_namespace(ns)
    store = MemoryStore(db.conn, embedder=FakeEmbedder())

    m1 = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="alpha and gamma",
        content="alpha gamma",
    )
    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="beta and delta",
        content="beta delta",
    )
    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="alpha gamma and beta",
        content="alpha gamma beta extras",
    )

    results = search(
        db.conn,
        query="alpha gamma",
        namespace_id=ns_id,
        embedder=FakeEmbedder(),
        limit=5,
    )
    ids = [r.id for r in results]
    # Semantically-purest "alpha gamma" memory should rank first.
    assert ids[0] == m1.id


def test_search_works_without_embedder(db: Database):
    """No embedder → rank-based BM25 still returns sensibly-ordered results."""
    ns = NamespaceManager(db.conn, None)
    ns_id = _seed_namespace(ns)
    store = MemoryStore(db.conn)  # null embedder

    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="Cats are mammals",
        content="all cats are mammals",
    )
    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="Dogs bark",
        content="dogs go woof",
    )

    results = search(
        db.conn,
        query="cats mammals",
        namespace_id=ns_id,
        embedder=None,
        limit=5,
    )
    assert len(results) >= 1
    assert results[0].title == "Cats are mammals"
    # Without weights, score is the fused relevance — top match should be > 0.
    assert results[0].score is not None and results[0].score > 0


def test_mismatched_dim_embedding_is_filtered(db: Database):
    """An embedding row written with one dim should NOT be combined with a
    differently-dim'd active embedder. The row is silently skipped."""
    ns = NamespaceManager(db.conn, None)
    ns_id = _seed_namespace(ns)
    store = MemoryStore(db.conn, embedder=FakeEmbedder())
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="thing",
        content="alpha",
    )

    # Active dim still 4 — but pretend the row is from an 8-dim model.
    db.conn.execute("UPDATE memory_embeddings SET dim = 8 WHERE memory_id = ?", (mem.id,))
    db.conn.commit()

    assert store.get_embedding(mem.id) is None
    assert store.get_embeddings_for([mem.id]) == {}
