"""Relevance must not depend on how many rows the caller asked for.

`search(q, limit=k)` must be the first k of `search(q, limit=K)`. It was not:
the semantic cohort was sized `limit * 4` (plus `limit // 2` entrants), so a
memory's semantic RANK - and therefore its relevance, and therefore the result
order - moved with `limit`. One memory scored 0.9439 / 0.9379 / 0.9245 / 0.9172
on a single query against the real brain, varying nothing else. A caller
narrowing the request to be precise got a different, worse answer.

These are pure invariants: no golden set, no corpus quality judgement. They say
only that the same query must mean the same thing at every depth.

The embedder is injected deliberately. The suite runs with embeddings off, and
BM25-only relevance is already limit-invariant - so with the default fixture
these tests would pass against the *broken* code and prove nothing.
"""

from __future__ import annotations

import pytest

from gingugu import search as search_mod
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


class GradedEmbedder:
    """Deterministic embedder over a small vocabulary.

    Counts give graded, reproducible similarities rather than the near-binary
    ones a two-token embedder produces - the point here is that many memories
    hold *distinct* semantic ranks, so a change in cohort size actually moves
    them.
    """

    model_name = "fake-graded-model"
    dim = 6
    enabled = True

    _VOCAB = ("resume", "sail", "board", "backup", "uncommitted", "ranking")

    def encode(self, text: str) -> list[float]:
        low = text.lower()
        # +1 keeps every vector non-zero, so cosine is always defined.
        return [1.0 + float(low.count(k)) for k in self._VOCAB]

    def encode_many(self, texts):
        return [self.encode(t) for t in texts]


# The corpus MUST be larger than the deepest pool under test, or the truncation
# never bites and these tests pass against the broken code. With 60 rows the old
# `limit * 4` pool held 4 at limit=1 and 40 at limit=10, so the semantic cohort
# really did change size; with 12 rows every limit saw the whole corpus and the
# bug was invisible.
_CORPUS_SIZE = 60

# Every row carries "sail" so BM25 returns the whole corpus and the pool LIMIT
# is what truncates it, not the match. The rest varies the vocabulary counts so
# rows hold distinct, graded semantic ranks.
_SHAPES = [
    "resume sail uncommitted uncommitted board",
    "resume sail uncommitted board board",
    "resume sail board ranking",
    "resume resume sail uncommitted",
    "board board board sail",
    "backup backup board sail",
    "backup board ranking sail",
    "backup backup backup sail",
    "ranking ranking board sail",
    "ranking ranking ranking sail",
    "uncommitted uncommitted resume sail",
    "resume board uncommitted sail",
]
_CORPUS = [
    (f"sail entry {i} resume board", f"{_SHAPES[i % len(_SHAPES)]} entry {i}")
    for i in range(_CORPUS_SIZE)
]

QUERIES = [
    "resume sail uncommitted board",
    "backup sail board",
    "sail",
]


@pytest.fixture
def corpus(store: MemoryStore, namespaces: NamespaceManager):
    ns = namespaces.get_or_create("invariance")
    estore = MemoryStore(store.conn, embedder=GradedEmbedder())
    for title, content in _CORPUS:
        estore.create(namespace_id=ns.id, type=MemoryType.FACT, title=title, content=content)
    return store, ns


def _search(store: MemoryStore, ns_id: str, query: str, limit: int):
    # No `weights`, so `score` is the fused relevance itself rather than the
    # composite - this is a claim about relevance, not about decay.
    return search_mod.search(
        store.conn,
        query=query,
        namespace_id=ns_id,
        limit=limit,
        embedder=GradedEmbedder(),
    )


def _ids(store: MemoryStore, ns_id: str, query: str, limit: int) -> list[str]:
    return [m.id for m in _search(store, ns_id, query, limit)]


@pytest.mark.parametrize("query", QUERIES)
@pytest.mark.parametrize("shallow", [1, 2, 3, 5])
def test_a_shallow_call_is_a_prefix_of_a_deep_one(corpus, query, shallow):
    """The core invariant: asking for fewer rows must not change which rows."""
    store, ns = corpus
    deep = _ids(store, ns.id, query, 10)
    assert _ids(store, ns.id, query, shallow) == deep[:shallow]


@pytest.mark.parametrize("query", QUERIES)
def test_relevance_of_a_memory_does_not_move_with_limit(corpus, query):
    """The mechanism behind the ordering bug, asserted directly.

    Ordering can survive a wobble in the underlying numbers, so pin the numbers
    too: a memory's relevance is a property of the query, not of the request
    size.
    """
    store, ns = corpus
    baseline = {m.id: m.score for m in _search(store, ns.id, query, 10)}
    for limit in (1, 2, 3, 5, 8):
        for mem in _search(store, ns.id, query, limit):
            assert mem.id in baseline, "a shallow call surfaced a memory a deep one did not"
            assert mem.score == pytest.approx(
                baseline[mem.id], abs=1e-9
            ), f"relevance of {mem.id} moved between limit={limit} and limit=10"


def test_tied_scores_order_deterministically(store: MemoryStore, namespaces: NamespaceManager):
    """Equal relevance must not mean arbitrary order.

    RRF maps a swapped rank pair to identical floats: a memory ranked (bm25 1,
    semantic 2) scores exactly what one ranked (2, 1) scores. Ties are therefore
    routine, and the order among them used to fall out of `_fuse_ranks`
    iterating a `set` - so the same query on the same data returned tied
    memories in a different order from one process to the next. Measured on the
    real brain: 2 of 10 query/namespace pairs reordered between two runs of
    *identical* code. Ties now break on id.

    The corpus below forces that swap rather than hoping for one: "keyword
    heavy" wins BM25 on term frequency, "embedding twin" is exactly parallel to
    the query vector and so wins the semantic rank.
    """
    ns = namespaces.get_or_create("ties")
    estore = MemoryStore(store.conn, embedder=GradedEmbedder())
    for title, content in [
        ("keyword heavy", "resume backup resume backup resume backup"),
        ("embedding twin", "resume backup"),
        ("filler one", "sail sail board"),
        ("filler two", "ranking uncommitted"),
    ]:
        estore.create(namespace_id=ns.id, type=MemoryType.FACT, title=title, content=content)

    results = _search(store, ns.id, "resume backup", 10)
    scores = [m.score for m in results]
    assert len(set(scores)) < len(scores), "corpus no longer produces a tie; test is vacuous"

    by_score: dict[float, list[str]] = {}
    for mem in results:
        by_score.setdefault(mem.score, []).append(mem.id)
    for score, ids in by_score.items():
        assert ids == sorted(ids), f"tied group at {score} is not in a deterministic order"


def test_invariance_holds_past_the_cohort_size(corpus):
    """A request deeper than the cohort still extends the shallow answer.

    Beyond ``_SEMANTIC_COHORT`` the BM25 pool grows to have enough rows to
    return. Those extra rows must not re-rank the ones already there, which is
    why they keep their BM25 rank and stay out of the semantic cohort.
    """
    store, ns = corpus
    query = "resume sail uncommitted board"
    deep = _ids(store, ns.id, query, 50)
    assert _ids(store, ns.id, query, 3) == deep[:3]
    assert _ids(store, ns.id, query, 10) == deep[:10]
