"""The write-time hints must report an ABSOLUTE similarity, not a rank artifact.

This is the regression suite for the defect where ``similar_memories`` and
``suggested_relations`` reported the fused RRF relevance from ``search()``. That
number is a function of a candidate's RANK in the retrieval pools, normalized so
rank 1 in both maps to 1.0, so the top hit trended toward 1.0 for every payload
ever written, and both hint thresholds were arithmetically unreachable. The
practical symptom: three "similar" memories and three suggestions on every
single store, whatever was stored.

The tests that matter here are the ones a rank-based score CANNOT pass:

* an unrelated payload gets an EMPTY hint list;
* the same pair scores the same whatever else is in the pool alongside it.

The suite runs with embeddings disabled, so these exercise the lexical branch.
``test_cosine_branch_is_used_when_embeddings_are_available`` covers the other.
"""

from __future__ import annotations

from gingugu.config import Config
from gingugu.database import Database
from gingugu.handlers import ServerContext, hints
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.similarity import (
    DEDUPE_MIN_SIMILARITY,
    RELATION_MIN_SIMILARITY,
    payload_similarity,
)
from gingugu.storage import MemoryStore

TITLE = "wal checkpoint stalls when a reader holds the snapshot open"
CONTENT = "sqlite cannot truncate the write ahead log while a long read transaction is live"
UNRELATED_TITLE = "harbour tide chart for the spring equinox"
UNRELATED_CONTENT = "high water at six twelve, wind south west at eleven knots, waxing gibbous"


def _ctx(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config
) -> ServerContext:
    return ServerContext(config=config, store=store, namespaces=namespaces, conn=db.conn)


def _create(store: MemoryStore, ns_id: str, title: str, content: str):
    return store.create(namespace_id=ns_id, type=MemoryType.FACT, title=title, content=content)


def test_unrelated_payload_gets_no_hints(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    """The headline regression: retrieval always has a best hit, the gate must
    still refuse it when nothing in the corpus is actually close."""
    ns_id = namespaces.get_or_create("test-ns").id
    corpus = [
        _create(store, ns_id, TITLE, CONTENT),
        _create(store, ns_id, "vacuum rewrites the database file", "reclaiming freelist pages"),
    ]
    # Retrieval hands back its best candidates, exactly as it does in production.
    monkeypatch.setattr(hints.search_mod, "search", lambda *a, **k: list(corpus))
    ctx = _ctx(db, store, namespaces, config)

    assert (
        hints.find_similar(
            ctx, namespace_id=ns_id, title=UNRELATED_TITLE, content=UNRELATED_CONTENT
        )
        == []
    )
    assert (
        hints.suggest_relations(
            ctx,
            memory_id=None,
            namespace_id=ns_id,
            title=UNRELATED_TITLE,
            content=UNRELATED_CONTENT,
        )
        == []
    )


def test_near_duplicate_payload_is_surfaced(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    twin = _create(store, ns_id, TITLE, CONTENT)
    monkeypatch.setattr(hints.search_mod, "search", lambda *a, **k: [twin])

    out = hints.find_similar(
        _ctx(db, store, namespaces, config), namespace_id=ns_id, title=TITLE, content=CONTENT
    )

    assert [m["id"] for m in out] == [twin.id]
    assert out[0]["similarity"] == 1.0
    assert out[0]["basis"] == "lexical"


def test_similarity_does_not_depend_on_the_rest_of_the_pool(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    """The defect in one assertion.

    A rank-derived score changes when the pool around a candidate changes: the
    same memory ranked 1st and 3rd carries two different numbers. An absolute
    measure compares two texts and nothing else, so the number a caller sees
    for a given pair must be identical either way.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    target = _create(store, ns_id, TITLE, CONTENT)
    padding = [_create(store, ns_id, f"unrelated note {i}", UNRELATED_CONTENT) for i in range(2)]
    ctx = _ctx(db, store, namespaces, config)

    monkeypatch.setattr(hints.search_mod, "search", lambda *a, **k: [target])
    alone = hints.find_similar(ctx, namespace_id=ns_id, title=TITLE, content=CONTENT)

    # Same pair, but now the candidate is last in a pool of three.
    monkeypatch.setattr(hints.search_mod, "search", lambda *a, **k: [*padding, target])
    crowded = hints.find_similar(ctx, namespace_id=ns_id, title=TITLE, content=CONTENT)

    assert [m["id"] for m in alone] == [target.id]
    assert [m["id"] for m in crowded] == [target.id]
    assert alone[0]["similarity"] == crowded[0]["similarity"]


def test_hints_do_not_leak_a_retrieval_score(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    """Two numbers under one payload, one of them meaningless, is worse than one."""
    ns_id = namespaces.get_or_create("test-ns").id
    twin = _create(store, ns_id, TITLE, CONTENT)
    twin.score = 0.9262  # what retrieval would have stamped on it
    monkeypatch.setattr(hints.search_mod, "search", lambda *a, **k: [twin])

    out = hints.find_similar(
        _ctx(db, store, namespaces, config), namespace_id=ns_id, title=TITLE, content=CONTENT
    )

    assert "score" not in out[0]
    assert "similarity" in out[0]


def test_relation_gate_is_softer_than_the_dedupe_gate() -> None:
    """Relation hits are candidates to examine; merge candidates must be closer.
    Both instruments have to agree on that ordering or the two lists could swap
    roles depending on whether embeddings happened to be available."""
    for basis in ("cosine", "lexical"):
        assert RELATION_MIN_SIMILARITY[basis] < DEDUPE_MIN_SIMILARITY[basis]


def test_lexical_similarity_is_symmetric_and_bounded(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = _create(store, ns_id, TITLE, CONTENT)
    b = _create(store, ns_id, UNRELATED_TITLE, UNRELATED_CONTENT)

    scores, basis = payload_similarity(
        db.conn, store.embedder, title=TITLE, content=CONTENT, memory_ids=[a.id, b.id]
    )

    assert basis == "lexical"
    assert scores[a.id] == 1.0
    assert 0.0 <= scores[b.id] < 0.1


def test_cosine_branch_is_used_when_embeddings_are_available(
    db: Database, namespaces: NamespaceManager
) -> None:
    """With a provider present the gate must switch instruments and say so.

    Uses a deterministic stub rather than the real model: the point is the
    branch and the reported ``basis``, not the quality of any one embedding.
    """

    class StubEmbedder:
        dim = 3
        model_name = "stub"
        enabled = True

        def encode(self, text: str) -> list[float]:
            # "wal" is the discriminating token between the two bodies below.
            return [1.0, 0.0, 0.0] if "wal" in text.lower() else [0.0, 1.0, 0.0]

        def encode_many(self, texts: list[str]) -> list[list[float]]:
            return [self.encode(t) for t in texts]

    store = MemoryStore(db.conn, embedder=StubEmbedder())
    ns_id = namespaces.get_or_create("test-ns").id
    same = store.create(namespace_id=ns_id, type=MemoryType.FACT, title=TITLE, content=CONTENT)
    other = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title=UNRELATED_TITLE,
        content=UNRELATED_CONTENT,
    )

    scores, basis = payload_similarity(
        db.conn,
        store.embedder,
        title=TITLE,
        content=CONTENT,
        memory_ids=[same.id, other.id],
    )

    assert basis == "cosine"
    assert scores[same.id] == 1.0
    assert scores[other.id] == 0.0
