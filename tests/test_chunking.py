"""Chunked embeddings: a memory past the encoder's window gets vectors for its tail.

The encoder reads only its first window of tokens and truncates the rest with no
signal, so a phrase past it was invisible to the semantic side of search. Each
memory is now split into window-sized pieces. At query time a keyword-pool
member is scored by ONE piece, chosen by which piece holds the most query words.
Cosine never chooses: letting the best-matching piece win gives every long
sibling several lucky chances, and measured on a real brain that cost the head
of each memory everything it gained at the tail.

``TruncatingEmbedder`` reproduces the defect in miniature: it counts keywords in
its first ``max_tokens`` words only, exactly as the real encoder drops its tail.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from gingugu import chunking
from gingugu import search as search_mod
from gingugu.embedding_sync import backfill
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore

_WORD = re.compile(r"\S+")


class TruncatingEmbedder:
    """4-dim keyword counts over the first ``max_tokens`` words; words are tokens."""

    model_name = "fake-truncating"
    dim = 4
    enabled = True
    max_tokens = 8
    _KEYS = ("alpha", "beta", "gamma", "delta")

    def token_offsets(self, text: str) -> list[tuple[int, int]]:
        return [m.span() for m in _WORD.finditer(text)]

    def encode(self, text: str) -> list[float]:
        head = " ".join(text.lower().split()[: self.max_tokens])
        return [float(head.count(k)) for k in self._KEYS]

    def encode_many(self, texts):
        return [self.encode(t) for t in texts]


class NoTokenizerEmbedder(TruncatingEmbedder):
    """A backend that cannot expose token offsets (Ollama, today)."""

    token_offsets = None  # type: ignore[assignment]


def _pieces(conn, memory_id: str) -> list[tuple[int, bool]]:
    rows = conn.execute(
        "SELECT chunk, embedding IS NOT NULL AS has_vec FROM memory_chunks "
        "WHERE memory_id = ? ORDER BY chunk",
        (memory_id,),
    ).fetchall()
    return [(r["chunk"], bool(r["has_vec"])) for r in rows]


# --- spans ------------------------------------------------------------------


def _offsets(n: int) -> list[tuple[int, int]]:
    return [(i * 2, i * 2 + 1) for i in range(n)]


def test_text_inside_the_window_is_one_head_piece():
    assert chunking.piece_spans(_offsets(8), window=8) == [(0, 15)]


def test_long_text_is_covered_end_to_end_with_overlap():
    offsets = _offsets(20)
    spans = chunking.piece_spans(offsets, window=8)
    assert spans[0] == (0, offsets[7][1])  # the head is exactly the encoder's window
    assert spans[-1][1] == offsets[-1][1]  # the tail is reached
    for (_, prev_end), (start, _) in zip(spans, spans[1:], strict=False):
        assert start < prev_end  # consecutive pieces overlap, so no phrase is cut in two


def test_every_piece_fits_the_window():
    offsets = _offsets(50)
    for start, end in chunking.piece_spans(offsets, window=8):
        inside = [o for o in offsets if o[0] >= start and o[1] <= end]
        assert len(inside) <= 8


# --- choosing a piece ---------------------------------------------------------


def test_the_piece_holding_the_most_query_words_is_chosen():
    pieces = ["alpha scaffolding text", "ledger replay finished", "exporter left"]
    assert chunking.choose_piece({"ledger", "replay"}, pieces) == 1


def test_a_tie_goes_to_the_head():
    """The head is what search used before pieces existed; a tail piece has to
    earn its place with more lexical evidence, not equal evidence."""
    assert chunking.choose_piece({"ledger"}, ["ledger here", "ledger there"]) == 0
    assert chunking.choose_piece({"absent"}, ["one", "two"]) == 0


def test_query_terms_drop_stopwords():
    assert chunking.query_terms("what did we say about the Ledger replay") == {
        "ledger",
        "replay",
    }


# --- write path -----------------------------------------------------------------


@pytest.fixture
def estore(store: MemoryStore) -> MemoryStore:
    return MemoryStore(store.conn, embedder=TruncatingEmbedder())


def _long(content_tail: str) -> str:
    return "alpha " * 12 + content_tail


def test_a_long_memory_stores_its_pieces(estore, namespaces: NamespaceManager):
    ns = namespaces.get_or_create("test-ns").id
    mem = estore.create(namespace_id=ns, type=MemoryType.FACT, title="t", content=_long("delta"))
    pieces = _pieces(estore.conn, mem.id)
    # Piece 0 is the head: its vector already lives in memory_embeddings.
    assert pieces[0] == (0, False)
    assert len(pieces) > 1 and all(has for _, has in pieces[1:])


def test_a_short_memory_stores_only_the_head_marker(estore, namespaces):
    ns = namespaces.get_or_create("test-ns").id
    mem = estore.create(namespace_id=ns, type=MemoryType.FACT, title="t", content="alpha beta")
    assert _pieces(estore.conn, mem.id) == [(0, False)]


def test_an_update_rewrites_the_pieces(estore, namespaces):
    ns = namespaces.get_or_create("test-ns").id
    mem = estore.create(namespace_id=ns, type=MemoryType.FACT, title="t", content=_long("delta"))
    estore.update(mem.id, content="alpha beta")
    assert _pieces(estore.conn, mem.id) == [(0, False)]


def test_deleting_a_memory_deletes_its_pieces(estore, namespaces):
    ns = namespaces.get_or_create("test-ns").id
    mem = estore.create(namespace_id=ns, type=MemoryType.FACT, title="t", content=_long("delta"))
    estore.conn.execute("DELETE FROM memories WHERE id = ?", (mem.id,))
    assert _pieces(estore.conn, mem.id) == []


def test_a_backend_without_offsets_stores_no_pieces(store, namespaces):
    nstore = MemoryStore(store.conn, embedder=NoTokenizerEmbedder())
    ns = namespaces.get_or_create("test-ns").id
    mem = nstore.create(namespace_id=ns, type=MemoryType.FACT, title="t", content=_long("delta"))
    assert _pieces(store.conn, mem.id) == []


def test_backfill_pieces_memories_written_before_chunking(store, namespaces):
    """A store that predates this change has head vectors and no pieces."""
    ns = namespaces.get_or_create("test-ns").id
    old = MemoryStore(store.conn, embedder=NoTokenizerEmbedder())
    mem = old.create(namespace_id=ns, type=MemoryType.FACT, title="t", content=_long("delta"))
    assert _pieces(store.conn, mem.id) == []

    backfill(store.conn, TruncatingEmbedder(), batch_size=32)
    assert len(_pieces(store.conn, mem.id)) > 1
    assert backfill(store.conn, TruncatingEmbedder(), batch_size=32) == 0  # idempotent


# --- search ---------------------------------------------------------------------


def _two_siblings(estore, ns):
    # The target holds the query word only in its tail, past the 8-word window,
    # so its head vector is pure alpha. The sibling's head leans delta.
    target = estore.create(
        namespace_id=ns, type=MemoryType.FACT, title="handoff one", content=_long("ledger delta")
    )
    sibling = estore.create(
        namespace_id=ns,
        type=MemoryType.FACT,
        title="handoff two",
        content="ledger gamma delta alpha alpha alpha alpha alpha",
    )
    return target, sibling


def test_a_tail_phrase_now_reaches_its_memory(estore, namespaces):
    ns = namespaces.get_or_create("test-ns").id
    target, sibling = _two_siblings(estore, ns)
    ranks = search_mod.semantic_pool(
        estore.conn,
        "ledger delta",
        [],
        [],
        TruncatingEmbedder(),
        5,
        {target.id, sibling.id},
        {target.id, sibling.id},
    )
    assert ranks[target.id] == 1


def test_cosine_never_chooses_a_siblings_piece(estore, namespaces):
    """The multiple-comparisons guard. The sibling's tail is all delta, which is
    the best possible cosine match for the query - but it holds no query WORD, so
    it is not chosen and the sibling is scored on its head."""
    ns = namespaces.get_or_create("test-ns").id
    sibling = estore.create(
        namespace_id=ns,
        type=MemoryType.FACT,
        title="handoff",
        content="ledger alpha alpha alpha alpha alpha alpha alpha " + "delta " * 12,
    )
    embedder = TruncatingEmbedder()
    chosen = chunking.cohort_vectors(estore.conn, embedder, "ledger delta", [sibling.id])
    assert sibling.id not in chosen  # stays on its head vector


def test_entrants_are_scored_on_their_head(estore, namespaces):
    """Pieces are chosen only for the keyword pool. A memory outside it keeps the
    head vector search always used - measured, pieces add nothing there."""
    ns = namespaces.get_or_create("test-ns").id
    target, _ = _two_siblings(estore, ns)
    ranks = search_mod.semantic_pool(
        estore.conn, "ledger delta", [], [], TruncatingEmbedder(), 5, set(), set()
    )
    assert ranks is None or target.id not in ranks  # head is pure alpha: below the floor


# --- the real encoder -------------------------------------------------------------


@pytest.mark.bench_embeddings
@pytest.mark.timeout(180)  # cold-cache model download; see the fastembed CI-hang lesson
def test_real_encoder_pieces_fit_its_window():
    """The fakes above prove the logic; only the real model proves the window.

    ``max_tokens`` must come from the model's own config and every piece must
    fit inside it, or a piece is silently truncated - the defect pieces exist to
    fix, reintroduced one level down. Also pins that reading the tokenizer
    leaves the model's own truncation on.
    """
    from gingugu.embeddings import FastEmbedProvider

    provider = FastEmbedProvider()
    text = "the nightly ledger reconciliation job " * 300
    offsets = provider.token_offsets(text)
    assert offsets is not None and len(offsets) > provider.max_tokens > 0

    spans = chunking.piece_spans(offsets, provider.max_tokens)
    assert len(spans) > 1
    for start, end in spans:
        assert sum(1 for a, b in offsets if a >= start and b <= end) <= provider.max_tokens
    assert provider._model.model.tokenizer.truncation is not None


# --- never raises ----------------------------------------------------------------


class ExplodingEmbedder(TruncatingEmbedder):
    def encode_many(self, texts):
        raise RuntimeError("boom")


def test_a_failed_piece_write_leaves_the_memory_head_only(estore, namespaces):
    ns = namespaces.get_or_create("test-ns").id
    mem = estore.create(namespace_id=ns, type=MemoryType.FACT, title="t", content=_long("delta"))
    assert chunking.persist_pieces(estore.conn, ExplodingEmbedder(), mem.id, _long("x")) is False
    assert _pieces(estore.conn, mem.id) == []


def test_piece_selection_failure_degrades_to_head_vectors(estore, namespaces, monkeypatch):
    """Search must fall back, never fail: a broken piece read returns no overrides."""
    ns = namespaces.get_or_create("test-ns").id
    target, _ = _two_siblings(estore, ns)

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("no such table: memory_chunks")

    monkeypatch.setattr(chunking, "_cohort_vectors", broken)
    assert (
        chunking.cohort_vectors(estore.conn, TruncatingEmbedder(), "ledger delta", [target.id])
        == {}
    )
