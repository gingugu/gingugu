"""Keeps `memory_embeddings` in step with `memories`.

Split out of `storage.py` because the invariant it maintains is not
`MemoryStore`'s private business - it belongs to whoever writes a memory row,
and `MemoryStore` is not the only one. `memory_import` also inserts memory
rows, and for as long as this logic lived inside `MemoryStore` it had no way to
honor the invariant: imported memories landed FTS5-searchable (the full-text
index has triggers) but semantically invisible, with `memory_embeddings`
untouched. **There are no triggers for embeddings.** A memory's vector exists
only because some code path deliberately wrote it.

The repair path that did exist was not one: `backfill()` runs once at server
startup with `batch_size=32`, so a 1,423-memory import needed 45 restarts to
become searchable, and the "subsequent writes will surface the rest" reasoning
never applied to imported rows, which are never written again.

Every function here takes `(conn, embedder)` rather than a store, so any writer
can call it without depending on the memory CRUD layer.

Failures are swallowed and logged throughout: search degrades gracefully to
BM25-only when a vector is missing, so an encoding failure must never block or
roll back the write it accompanies.
"""

from __future__ import annotations

import logging
import sqlite3

from . import embeddings as emb
from .embeddings import EmbeddingProvider, embedding_input
from .models import utcnow_iso

logger = logging.getLogger(__name__)

_UPSERT = (
    "INSERT INTO memory_embeddings(memory_id, model, dim, embedding, "
    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(memory_id) DO UPDATE SET "
    "model=excluded.model, dim=excluded.dim, embedding=excluded.embedding, "
    "updated_at=excluded.updated_at"
)


def _enabled(embedder: EmbeddingProvider | None) -> bool:
    return embedder is not None and bool(getattr(embedder, "enabled", False))


def persist_one(
    conn: sqlite3.Connection,
    embedder: EmbeddingProvider | None,
    memory_id: str,
    title: str,
    content: str,
) -> None:
    """Encode one memory and upsert its vector. Best-effort."""
    if not _enabled(embedder):
        return
    try:
        vec = embedder.encode(embedding_input(title, content))
    except Exception:
        logger.exception("encode failed for memory %s; skipping embedding", memory_id)
        return
    if vec is None:
        return
    now = utcnow_iso()
    try:
        conn.execute(_UPSERT, (memory_id, embedder.model_name, len(vec), emb.pack(vec), now, now))
        conn.commit()
    except Exception:
        logger.exception("persist_embedding failed for memory %s", memory_id)


def get_one(
    conn: sqlite3.Connection, embedder: EmbeddingProvider | None, memory_id: str
) -> list[float] | None:
    """The stored vector for a memory, or None if absent or from another model."""
    row = conn.execute(
        "SELECT model, dim, embedding FROM memory_embeddings WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    if row is None:
        return None
    # Mismatched-model embeddings are intentionally hidden - combining vectors
    # from different models silently produces garbage. They get re-encoded on
    # the next write or by `backfill`.
    if _enabled(embedder) and embedder.dim and row["dim"] != embedder.dim:
        return None
    return emb.unpack(row["embedding"])


def get_many(
    conn: sqlite3.Connection, embedder: EmbeddingProvider | None, memory_ids: list[str]
) -> dict[str, list[float]]:
    """Bulk fetch keyed by memory_id. Mismatched-model rows are filtered out."""
    if not memory_ids:
        return {}
    placeholders = ", ".join("?" for _ in memory_ids)
    rows = conn.execute(
        f"SELECT memory_id, model, dim, embedding FROM memory_embeddings "
        f"WHERE memory_id IN ({placeholders})",
        memory_ids,
    ).fetchall()
    active_dim = embedder.dim if _enabled(embedder) else 0
    out: dict[str, list[float]] = {}
    for r in rows:
        if active_dim and r["dim"] != active_dim:
            continue
        out[r["memory_id"]] = emb.unpack(r["embedding"])
    return out


def unembedded_ids(
    conn: sqlite3.Connection, embedder: EmbeddingProvider | None, *, limit: int = 100
) -> list[str]:
    """IDs of memories with no vector at the active model's dimension."""
    if not _enabled(embedder):
        return []
    active_dim = embedder.dim
    if not active_dim:
        # The embedder has not initialized yet (fastembed sets `dim` lazily on
        # first encode). Return memories with no embedding at all and let the
        # caller's encode drive initialization.
        rows = conn.execute(
            "SELECT m.id FROM memories m "
            "LEFT JOIN memory_embeddings e ON e.memory_id = m.id "
            "WHERE e.memory_id IS NULL LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT m.id FROM memories m "
            "LEFT JOIN memory_embeddings e ON e.memory_id = m.id "
            "WHERE e.memory_id IS NULL OR e.dim != ? LIMIT ?",
            (active_dim, limit),
        ).fetchall()
    return [r["id"] for r in rows]


def embed_ids(
    conn: sqlite3.Connection,
    embedder: EmbeddingProvider | None,
    memory_ids: list[str],
    *,
    batch_size: int = 32,
) -> int:
    """Encode and persist vectors for the named memories. Returns rows written.

    Unlike `backfill`, this embeds EVERY id given rather than one batch's
    worth: a caller that just wrote 500 rows knows exactly which ones need
    vectors and should not have to poll for them. `batch_size` bounds how many
    are encoded per round trip, not how many are done in total.

    Ids already carrying a current-model vector are re-encoded rather than
    skipped, which keeps the function honest after a title/content change.
    """
    if not _enabled(embedder) or not memory_ids:
        return 0
    written = 0
    now = utcnow_iso()
    for start in range(0, len(memory_ids), batch_size):
        chunk = memory_ids[start : start + batch_size]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, title, content FROM memories WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        if not rows:
            continue
        texts = [embedding_input(r["title"], r["content"]) for r in rows]
        try:
            vectors = embedder.encode_many(texts)
        except Exception:
            logger.exception("batch encode failed for %d memories; skipping batch", len(rows))
            continue
        for r, vec in zip(rows, vectors, strict=False):
            if vec is None:
                continue
            try:
                conn.execute(
                    _UPSERT,
                    (r["id"], embedder.model_name, len(vec), emb.pack(vec), now, now),
                )
                written += 1
            except Exception:
                logger.exception("embedding write failed for memory %s", r["id"])
    if written:
        conn.commit()
    return written


def backfill(
    conn: sqlite3.Connection, embedder: EmbeddingProvider | None, *, batch_size: int = 32
) -> int:
    """Encode one batch of memories that are missing a current-model vector.

    Deliberately bounded: this runs at server startup, where the cost of a
    cold model download must not block the process. It is a safety net for
    rows that predate an embedding upgrade, NOT the repair path for a bulk
    import - that caller knows its own ids and should use `embed_ids`.
    """
    ids = unembedded_ids(conn, embedder, limit=batch_size)
    if not ids:
        return 0
    written = embed_ids(conn, embedder, ids, batch_size=batch_size)
    if written:
        logger.info("Backfilled %d embeddings", written)
    return written
