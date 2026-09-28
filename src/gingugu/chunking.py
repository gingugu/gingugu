"""Pieces: vectors for the part of a memory the encoder never reads.

An encoder reads a fixed window of tokens - 512 for the default bge-small, two of
them reserved - and truncates the rest with no error and no log. Measured on a
real brain (2026-09-27), 79.1% of memories are longer than that, so for four in
five the semantic side of search had never seen anything past the opening.
Probes asking for a phrase in a memory's tail scored 41% lower mrr than probes
asking for one in its head.

So a memory is split into window-sized PIECES with a small overlap, and every
piece past the first gets its own vector. Piece 0 is the head: its vector is the
one ``memory_embeddings`` already holds (it IS the truncated window), so its row
here carries the span and no vector, and doubles as the marker that the memory
has been pieced at all.

HOW A PIECE IS USED, and why it is not "best match wins". Scoring a memory by its
best-matching piece was measured first and it is a wash: every long sibling gets
several chances to match any query, those lucky pieces bury the memories whose
answer sits in their head, and the tail gain is paid back in full. Instead a
memory in the keyword pool is scored by exactly ONE piece, chosen LEXICALLY - the
piece holding the most distinct query words, ties to the head. Cosine never
chooses, so a sibling gets one shot, not several. Memories outside the keyword
pool keep their head vector, because measured there pieces add nothing.

Only a backend that exposes token offsets (``token_offsets`` + ``max_tokens``)
is pieced; spans must be cut on the encoder's own tokens or a piece silently
overflows the window it exists to fit. Any other backend keeps head-only search.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from . import embeddings as emb
from .embedding_text import embedding_input
from .embeddings import EmbeddingProvider
from .models import utcnow_iso

logger = logging.getLogger(__name__)

# Words that identify nothing. A piece is chosen by how many of the query's
# CONTENT words it holds, and a wrapper like "what did we say about" would
# otherwise hand every piece the same free matches.
QUERY_STOPWORDS = frozenset("""the a an and or but is was are were be been to of in on at for
    with that this it as by from not no so if then than we i my our its has had have do does
    did will would can could one two all any what which about say said""".split())

_WORD = re.compile(r"\w+", re.UNICODE)


def _offsets(embedder: EmbeddingProvider | None, text: str) -> list[tuple[int, int]] | None:
    fn = getattr(embedder, "token_offsets", None)
    if embedder is None or not getattr(embedder, "enabled", False) or not callable(fn):
        return None
    try:
        return fn(text)
    except Exception:  # pragma: no cover - defensive
        logger.exception("token_offsets failed; storing no pieces")
        return None


def piece_spans(offsets: list[tuple[int, int]], window: int) -> list[tuple[int, int]]:
    """Character spans of each piece. Piece 0 is exactly the encoder's window.

    Consecutive pieces overlap by an eighth of the window, so a phrase that falls
    across a boundary is still whole inside one piece.
    """
    if len(offsets) <= window:
        return [(0, offsets[-1][1] if offsets else 0)]
    overlap = max(1, window // 8)
    spans = [(0, offsets[window - 1][1])]
    end = window
    while end < len(offsets):
        start = end - overlap
        end = min(start + window, len(offsets))
        spans.append((offsets[start][0], offsets[end - 1][1]))
    return spans


def query_terms(query: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(query)} - QUERY_STOPWORDS


def choose_piece(terms: set[str], pieces: list[str]) -> int:
    """Index of the piece holding the most distinct query terms; ties go to the head."""
    best, best_count = 0, -1
    for i, text in enumerate(pieces):
        count = len(terms & {w.lower() for w in _WORD.findall(text)})
        if count > best_count:
            best, best_count = i, count
    return best


def persist_pieces(
    conn: sqlite3.Connection, embedder: EmbeddingProvider | None, memory_id: str, text: str
) -> bool:
    """Replace a memory's pieces. Best-effort, and never partial.

    Any failure leaves the memory with NO piece rows, which search reads as
    head-only - the behaviour before pieces existed - rather than with spans
    that no longer match its text. Never raises. The caller commits.
    """
    try:
        return _replace_pieces(conn, embedder, memory_id, text)
    except Exception:
        logger.exception("storing pieces failed for memory %s; it stays head-only", memory_id)
        try:
            conn.execute("DELETE FROM memory_chunks WHERE memory_id = ?", (memory_id,))
        except Exception:  # pragma: no cover - the table itself is unusable
            pass
        return False


def _replace_pieces(
    conn: sqlite3.Connection, embedder: EmbeddingProvider | None, memory_id: str, text: str
) -> bool:
    conn.execute("DELETE FROM memory_chunks WHERE memory_id = ?", (memory_id,))
    offsets = _offsets(embedder, text)
    if offsets is None:
        return False
    spans = piece_spans(offsets, embedder.max_tokens)
    try:
        vectors = embedder.encode_many([text[s:e] for s, e in spans[1:]]) if spans[1:] else []
    except Exception:
        logger.exception("piece encode failed for memory %s", memory_id)
        return False
    if any(v is None for v in vectors):
        return False
    now = utcnow_iso()
    dim = len(vectors[0]) if vectors else embedder.dim
    rows = [(memory_id, 0, spans[0][0], spans[0][1], embedder.model_name, dim, None, now)]
    rows += [
        (memory_id, i, s, e, embedder.model_name, dim, emb.pack(v), now)
        for i, ((s, e), v) in enumerate(zip(spans[1:], vectors, strict=True), start=1)
    ]
    conn.executemany("INSERT INTO memory_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return True


def backfill(
    conn: sqlite3.Connection, embedder: EmbeddingProvider | None, *, batch_size: int = 32
) -> int:
    """Piece one batch of memories that have a head vector but no pieces yet."""
    if _offsets(embedder, "probe") is None:
        return 0
    rows = conn.execute(
        "SELECT m.id, m.title, m.content, m.about FROM memories m "
        "JOIN memory_embeddings e ON e.memory_id = m.id AND e.dim = ? "
        "WHERE NOT EXISTS (SELECT 1 FROM memory_chunks c "
        "                  WHERE c.memory_id = m.id AND c.chunk = 0 AND c.dim = e.dim) "
        "LIMIT ?",
        (embedder.dim, batch_size),
    ).fetchall()
    done = sum(
        persist_pieces(
            conn, embedder, r["id"], embedding_input(r["title"], r["content"], r["about"])
        )
        for r in rows
    )
    conn.commit()
    return done


def cohort_vectors(
    conn: sqlite3.Connection,
    embedder: EmbeddingProvider,
    query: str,
    memory_ids: list[str],
) -> dict[str, list[float]]:
    """For each memory whose lexically chosen piece is NOT its head, that piece's vector.

    A memory absent from the result is scored on its head vector, as before.
    Never raises: any failure returns {} and search degrades to head vectors.
    """
    try:
        return _cohort_vectors(conn, embedder, query, memory_ids)
    except Exception:
        logger.exception("piece selection failed; scoring on head vectors")
        return {}


def _cohort_vectors(
    conn: sqlite3.Connection,
    embedder: EmbeddingProvider,
    query: str,
    memory_ids: list[str],
) -> dict[str, list[float]]:
    if not memory_ids:
        return {}
    terms = query_terms(query)
    placeholders = ", ".join("?" for _ in memory_ids)
    spans: dict[str, list[tuple[int, int, bytes | None]]] = {}
    for r in conn.execute(
        f"SELECT memory_id, start_char, end_char, embedding FROM memory_chunks "
        f"WHERE dim = ? AND memory_id IN ({placeholders}) ORDER BY memory_id, chunk",
        [embedder.dim, *memory_ids],
    ):
        row = (r["start_char"], r["end_char"], r["embedding"])
        spans.setdefault(r["memory_id"], []).append(row)
    pieced = [mid for mid, rows in spans.items() if len(rows) > 1]
    if not pieced:
        return {}
    marks = ", ".join("?" for _ in pieced)
    texts = {
        r["id"]: embedding_input(r["title"], r["content"], r["about"])
        for r in conn.execute(
            f"SELECT id, title, content, about FROM memories WHERE id IN ({marks})", pieced
        )
    }
    out: dict[str, list[float]] = {}
    for mid in pieced:
        if mid not in texts:  # deleted between the two reads: head-only, like any other
            continue
        rows = spans[mid]
        chosen = choose_piece(terms, [texts[mid][s:e] for s, e, _ in rows])
        if chosen and rows[chosen][2] is not None:
            out[mid] = emb.unpack(rows[chosen][2])
    return out
