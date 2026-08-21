"""``sort_by`` must order the corpus, not a pool chosen by another ordering.

Every test here builds a corpus larger than the candidate pool the old
implementation used (``limit * 4``) and puts the rows that should win the
sort *outside* that pool. Run any of them against the previous code and the
answer is drawn from the wrong candidate set: the newest memory is absent
rather than merely mis-ranked, which is the failure a caller cannot detect
(a miss looks exactly like an answer).
"""

from __future__ import annotations

from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.search_filters import advanced_search
from gingugu.storage import MemoryStore

WEIGHTS = {"relevance": 0.45, "freshness": 0.25, "access": 0.10, "confidence": 0.20}

# Sits comfortably inside the old `limit * 4` pool for every limit used here.
_CORPUS = 20


def _stamp(store: MemoryStore, mem_id: str, *, created: str, accessed: str) -> None:
    store.conn.execute(
        "UPDATE memories SET created_at = ?, last_accessed = ? WHERE id = ?",
        (created, accessed, mem_id),
    )


def _seed_newest_are_coldest(store: MemoryStore, ns_id: str) -> list[str]:
    """Corpus where recency of creation is the inverse of recency of access.

    The five newest memories are the five least recently read, so a pool
    truncated by ``last_accessed`` cannot contain any of them. Returns the
    newest-first ids.
    """
    newest: list[str] = []
    for i in range(_CORPUS):
        mem = store.create(
            namespace_id=ns_id,
            type=MemoryType.FACT,
            title=f"mem{i:02d}",
            content="findme common",
        )
        if i < 5:
            # Newly created, never read since.
            _stamp(
                store,
                mem.id,
                created=f"2026-08-{21 - i:02d}T00:00:00+00:00",
                accessed="2020-01-01T00:00:00+00:00",
            )
            newest.append(mem.id)
        else:
            # Old, but read moments ago - these are what fills the old pool.
            _stamp(
                store,
                mem.id,
                created="2020-01-01T00:00:00+00:00",
                accessed=f"2026-08-21T00:00:{i:02d}+00:00",
            )
    store.conn.commit()
    return newest


def test_sort_created_returns_the_true_newest_without_a_query(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    newest = _seed_newest_are_coldest(store, ns_id)

    results = advanced_search(
        store.conn, namespace_id=ns_id, sort_by="created", limit=3, weights=WEIGHTS
    )

    # Not "the newest of the 12 most recently read" - the newest, full stop.
    assert [m.id for m in results] == newest[:3]


def test_sort_created_without_a_query_is_limit_invariant(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    _seed_newest_are_coldest(store, ns_id)

    shallow = advanced_search(
        store.conn, namespace_id=ns_id, sort_by="created", limit=3, weights=WEIGHTS
    )
    deep = advanced_search(
        store.conn, namespace_id=ns_id, sort_by="created", limit=15, weights=WEIGHTS
    )

    # Asking for fewer rows must narrow the answer, never change it.
    assert [m.id for m in shallow] == [m.id for m in deep][:3]


def test_score_sort_without_a_query_ranks_the_whole_corpus(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # The composite is computed in Python, so the fix is to score every
    # matching row rather than every row of a pool picked by last_accessed.
    ns_id = namespaces.get_or_create("test-ns").id
    for i in range(_CORPUS):
        mem = store.create(
            namespace_id=ns_id,
            type=MemoryType.FACT,
            title=f"filler{i:02d}",
            content="c",
            confidence=Confidence.INFERRED,
        )
        _stamp(
            store,
            mem.id,
            created="2020-01-01T00:00:00+00:00",
            accessed=f"2026-08-21T00:00:{i:02d}+00:00",
        )
    best = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="fresh and verified",
        content="c",
        confidence=Confidence.VERIFIED,
    )
    # Freshest and most confident, and colder than every filler - so it loses
    # a last_accessed cut despite winning the sort it was asked for.
    _stamp(
        store, best.id, created="2026-08-21T00:00:00+00:00", accessed="2020-01-01T00:00:00+00:00"
    )
    store.conn.commit()

    results = advanced_search(
        store.conn, namespace_id=ns_id, sort_by="decay_score", limit=2, weights=WEIGHTS
    )

    assert results[0].id == best.id


def _seed_newest_match_worst(store: MemoryStore, ns_id: str) -> list[str]:
    """Corpus where the newest rows are the weakest keyword matches.

    Old rows repeat both query terms; new rows carry one term once, so BM25
    ranks them last and a relevance-truncated pool drops them. Returns the
    newest-first ids.
    """
    newest: list[str] = []
    for i in range(_CORPUS):
        strong = i >= 4
        mem = store.create(
            namespace_id=ns_id,
            type=MemoryType.FACT,
            title=f"mem{i:02d}",
            content="findme common findme common findme common" if strong else "findme",
        )
        if strong:
            _stamp(
                store,
                mem.id,
                created="2020-01-01T00:00:00+00:00",
                accessed="2020-01-01T00:00:00+00:00",
            )
        else:
            _stamp(
                store,
                mem.id,
                created=f"2026-08-{21 - i:02d}T00:00:00+00:00",
                accessed=f"2026-08-{21 - i:02d}T00:00:00+00:00",
            )
            newest.append(mem.id)
    store.conn.commit()
    return newest


def test_sort_created_with_a_query_returns_the_newest_match(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    newest = _seed_newest_match_worst(store, ns_id)

    results = advanced_search(
        store.conn,
        query="findme common",
        namespace_id=ns_id,
        sort_by="created",
        limit=2,
        weights=WEIGHTS,
    )

    # A date sort over an FTS match set means the newest thing that matches,
    # not the newest thing among the best matches.
    assert [m.id for m in results] == newest[:2]


def test_sort_accessed_with_a_query_returns_the_most_recently_read_match(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    newest = _seed_newest_match_worst(store, ns_id)

    results = advanced_search(
        store.conn,
        query="findme common",
        namespace_id=ns_id,
        sort_by="accessed",
        limit=2,
        weights=WEIGHTS,
    )

    assert [m.id for m in results] == newest[:2]


def test_sort_created_with_a_query_is_limit_invariant(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    _seed_newest_match_worst(store, ns_id)

    shallow = advanced_search(
        store.conn,
        query="findme common",
        namespace_id=ns_id,
        sort_by="created",
        limit=2,
        weights=WEIGHTS,
    )
    deep = advanced_search(
        store.conn,
        query="findme common",
        namespace_id=ns_id,
        sort_by="created",
        limit=12,
        weights=WEIGHTS,
    )

    assert [m.id for m in shallow] == [m.id for m in deep][:2]


def test_equal_timestamps_break_ties_on_id(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # Memories written in one operation share a timestamp. Without an explicit
    # tie-break the order among them is storage order, which is not a promise.
    ns_id = namespaces.get_or_create("test-ns").id
    ids = []
    for i in range(6):
        mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title=f"t{i}", content="c")
        _stamp(
            store,
            mem.id,
            created="2026-08-21T00:00:00+00:00",
            accessed="2026-08-21T00:00:00+00:00",
        )
        ids.append(mem.id)
    store.conn.commit()

    results = advanced_search(
        store.conn, namespace_id=ns_id, sort_by="created", limit=3, weights=WEIGHTS
    )

    assert [m.id for m in results] == sorted(ids)[:3]


def test_score_sort_without_weights_falls_back_to_listing_order(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # No weights means every score is an identical 0.5; ranking a flat tie
    # would return an arbitrary slice, so the listing order stands in.
    # Unlike the rest of this file this one also passes against the old code -
    # it pins the behaviour of the new fallback branch rather than guarding a
    # defect, and is labelled that way so nobody reads it as a regression test.
    ns_id = namespaces.get_or_create("test-ns").id
    ids = []
    for i in range(5):
        mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title=f"t{i}", content="c")
        _stamp(
            store,
            mem.id,
            created="2020-01-01T00:00:00+00:00",
            accessed=f"2026-08-21T00:00:{i:02d}+00:00",
        )
        ids.append(mem.id)
    store.conn.commit()

    results = advanced_search(store.conn, namespace_id=ns_id, sort_by="relevance", limit=3)

    assert [m.id for m in results] == list(reversed(ids))[:3]
    assert all(m.score == 0.5 for m in results)
