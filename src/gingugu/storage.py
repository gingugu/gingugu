"""CRUD operations for memories."""

from __future__ import annotations

import logging
import sqlite3
import uuid

from . import embeddings as emb
from .embeddings import EmbeddingProvider, NullEmbeddingProvider
from .models import Confidence, Memory, MemoryType, normalize_tag, utcnow_iso

logger = logging.getLogger(__name__)

_COLUMNS = (
    "id, namespace_id, type, title, content, confidence, source, "
    "created_at, updated_at, last_accessed, last_confirmed, access_count, metadata"
)


class MemoryStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._conn = conn
        self._embedder = embedder or NullEmbeddingProvider()

    @property
    def embedder(self) -> EmbeddingProvider:
        return self._embedder

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> Memory:
        return Memory(**dict(row))

    def create(
        self,
        *,
        namespace_id: str,
        type: MemoryType,
        title: str,
        content: str,
        confidence: Confidence = Confidence.INFERRED,
        source: str | None = None,
        metadata: str | None = None,
        tags: list[str] | None = None,
    ) -> Memory:
        now = utcnow_iso()
        mem = Memory(
            id=str(uuid.uuid4()),
            namespace_id=namespace_id,
            type=type,
            title=title,
            content=content,
            confidence=confidence,
            source=source,
            created_at=now,
            updated_at=now,
            last_accessed=now,
            last_confirmed=now if confidence == Confidence.VERIFIED else None,
            access_count=0,
            metadata=metadata,
        )
        self._conn.execute(
            f"INSERT INTO memories({_COLUMNS}) "
            "VALUES (:id, :namespace_id, :type, :title, :content, :confidence, :source, "
            ":created_at, :updated_at, :last_accessed, :last_confirmed, :access_count, :metadata)",
            {
                **mem.model_dump(exclude={"score", "tags"}),
                "type": mem.type.value,
                "confidence": mem.confidence.value,
            },
        )
        if tags:
            self.set_tags(mem.id, tags, commit=False)
        self._conn.commit()
        mem.tags = self.get_tags(mem.id)
        self._persist_embedding(mem.id, mem.title, mem.content)
        logger.info("Stored memory %s (%s)", mem.id, mem.title)
        return mem

    def get(self, memory_id: str, *, record_access: bool = True) -> Memory | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        if record_access:
            self._record_access(memory_id)
        mem = self._row_to_model(row)
        mem.tags = self.get_tags(memory_id)
        return mem

    def update(
        self,
        memory_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        confidence: Confidence | None = None,
        metadata: str | None = None,
    ) -> Memory | None:
        existing = self.get(memory_id, record_access=False)
        if existing is None:
            return None
        now = utcnow_iso()
        new_confidence = confidence or existing.confidence
        last_confirmed = existing.last_confirmed
        if confidence == Confidence.VERIFIED:
            last_confirmed = now
        # Empty string clears metadata to NULL (None means "leave unchanged" —
        # MCP optional params cannot distinguish absent from null).
        if metadata is None:
            new_metadata = existing.metadata
        elif metadata == "":
            new_metadata = None
        else:
            new_metadata = metadata
        new_title = title if title is not None else existing.title
        new_content = content if content is not None else existing.content
        self._conn.execute(
            "UPDATE memories SET title=?, content=?, confidence=?, metadata=?, "
            "updated_at=?, last_confirmed=? WHERE id=?",
            (
                new_title,
                new_content,
                new_confidence.value,
                new_metadata,
                now,
                last_confirmed,
                memory_id,
            ),
        )
        self._conn.commit()
        # Re-encode only when the text the embedding was derived from actually
        # changed — confidence/metadata updates don't invalidate the vector.
        if (title is not None and title != existing.title) or (
            content is not None and content != existing.content
        ):
            self._persist_embedding(memory_id, new_title, new_content)
        return self.get(memory_id, record_access=False)

    def delete(self, memory_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._prune_orphan_tags()
        self._conn.commit()
        return cur.rowcount > 0

    def _record_access(self, memory_id: str) -> None:
        now = utcnow_iso()
        self._conn.execute(
            "INSERT INTO access_log(id, memory_id, accessed_at) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), memory_id, now),
        )
        self._conn.execute(
            "UPDATE memories SET access_count = access_count + 1, last_accessed = ? "
            "WHERE id = ?",
            (now, memory_id),
        )
        self._conn.commit()

    def touch_many(self, memory_ids: list[str]) -> int:
        """Refresh ``last_accessed`` on memories without counting a real access.

        This is the **spreading-activation** primitive: when a memory is
        recalled, its related neighbours are *reactivated* — their dormancy
        clock resets — but this is not a direct access, so ``access_count`` is
        left untouched and no ``access_log`` row is written. Returns the number
        of rows refreshed.
        """
        ids = list(dict.fromkeys(mid for mid in memory_ids if mid))
        if not ids:
            return 0
        now = utcnow_iso()
        placeholders = ", ".join("?" for _ in ids)
        cur = self._conn.execute(
            f"UPDATE memories SET last_accessed = ? WHERE id IN ({placeholders})",
            (now, *ids),
        )
        self._conn.commit()
        return cur.rowcount

    # --- Tags ---------------------------------------------------------------

    def _get_or_create_tag(self, name: str) -> str:
        row = self._conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row is not None:
            return row["id"]
        tag_id = str(uuid.uuid4())
        self._conn.execute("INSERT INTO tags(id, name) VALUES (?, ?)", (tag_id, name))
        return tag_id

    def set_tags(self, memory_id: str, tags: list[str], *, commit: bool = True) -> list[str]:
        """Replace all tags on a memory with the normalized, de-duplicated set."""
        normalized = list(dict.fromkeys(normalize_tag(t) for t in tags if t.strip()))
        self._conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory_id,))
        for name in normalized:
            tag_id = self._get_or_create_tag(name)
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_tags(memory_id, tag_id) VALUES (?, ?)",
                (memory_id, tag_id),
            )
        self._prune_orphan_tags()
        if commit:
            self._conn.commit()
        return normalized

    def _prune_orphan_tags(self) -> None:
        """Drop tags rows no memory references (keeps the tags table from growing)."""
        self._conn.execute(
            "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM memory_tags)"
        )

    def add_tags(self, memory_id: str, tags: list[str], *, commit: bool = True) -> list[str]:
        """Add tags to a memory without removing existing ones."""
        for name in (normalize_tag(t) for t in tags if t.strip()):
            tag_id = self._get_or_create_tag(name)
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_tags(memory_id, tag_id) VALUES (?, ?)",
                (memory_id, tag_id),
            )
        if commit:
            self._conn.commit()
        return self.get_tags(memory_id)

    def get_tags(self, memory_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT t.name FROM tags t "
            "JOIN memory_tags mt ON mt.tag_id = t.id "
            "WHERE mt.memory_id = ? ORDER BY t.name",
            (memory_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def load_tags(self, memories: list[Memory]) -> None:
        """Batch-populate ``.tags`` on a list of Memory objects."""
        for mem in memories:
            mem.tags = self.get_tags(mem.id)

    # --- Embeddings ---------------------------------------------------------

    @staticmethod
    def _embedding_input(title: str, content: str) -> str:
        """The text fed into the embedder. Title carries strong signal so we
        prepend it — fastembed's BGE models handle short prefixes well."""
        return f"{title}\n\n{content}"

    def _persist_embedding(self, memory_id: str, title: str, content: str) -> None:
        """Encode the (title, content) tuple and upsert into memory_embeddings.

        Errors are swallowed — search degrades gracefully to BM25-only when an
        embedding is missing. We never let an encoding failure block a write.
        """
        if not self._embedder.enabled:
            return
        try:
            vec = self._embedder.encode(self._embedding_input(title, content))
        except Exception:
            logger.exception("encode failed for memory %s; skipping embedding", memory_id)
            return
        if vec is None:
            return
        now = utcnow_iso()
        try:
            self._conn.execute(
                "INSERT INTO memory_embeddings(memory_id, model, dim, embedding, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(memory_id) DO UPDATE SET "
                "model=excluded.model, dim=excluded.dim, embedding=excluded.embedding, "
                "updated_at=excluded.updated_at",
                (
                    memory_id,
                    self._embedder.model_name,
                    len(vec),
                    emb.pack(vec),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        except Exception:
            logger.exception("persist_embedding failed for memory %s", memory_id)

    def get_embedding(self, memory_id: str) -> list[float] | None:
        """Return the stored embedding for a memory, or None if absent or
        encoded with a model whose dim doesn't match the active provider."""
        row = self._conn.execute(
            "SELECT model, dim, embedding FROM memory_embeddings WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        # Mismatched-model embeddings are intentionally hidden — combining
        # vectors from different models silently produces garbage. They'll
        # get re-encoded on the next write or via the backfill path.
        if self._embedder.enabled and self._embedder.dim and row["dim"] != self._embedder.dim:
            return None
        return emb.unpack(row["embedding"])

    def get_embeddings_for(self, memory_ids: list[str]) -> dict[str, list[float]]:
        """Bulk fetch embeddings keyed by memory_id. Mismatched-model rows
        are filtered out (see get_embedding)."""
        if not memory_ids:
            return {}
        placeholders = ", ".join("?" for _ in memory_ids)
        rows = self._conn.execute(
            f"SELECT memory_id, model, dim, embedding FROM memory_embeddings "
            f"WHERE memory_id IN ({placeholders})",
            memory_ids,
        ).fetchall()
        active_dim = self._embedder.dim if self._embedder.enabled else 0
        out: dict[str, list[float]] = {}
        for r in rows:
            if active_dim and r["dim"] != active_dim:
                continue
            out[r["memory_id"]] = emb.unpack(r["embedding"])
        return out

    def list_unembedded_ids(self, *, limit: int = 100) -> list[str]:
        """IDs of memories without a current-model embedding (for backfill).

        A memory is considered unembedded if it has no row in
        memory_embeddings, or its row uses a different model dim than the
        active embedder.
        """
        if not self._embedder.enabled:
            return []
        active_dim = self._embedder.dim
        if not active_dim:
            # Embedder hasn't been initialized yet — return memories with no
            # embedding at all and let the backfill drive lazy init.
            rows = self._conn.execute(
                "SELECT m.id FROM memories m "
                "LEFT JOIN memory_embeddings e ON e.memory_id = m.id "
                "WHERE e.memory_id IS NULL LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT m.id FROM memories m "
                "LEFT JOIN memory_embeddings e ON e.memory_id = m.id "
                "WHERE e.memory_id IS NULL OR e.dim != ? LIMIT ?",
                (active_dim, limit),
            ).fetchall()
        return [r["id"] for r in rows]

    def backfill_embeddings(self, *, batch_size: int = 32) -> int:
        """Encode and persist embeddings for memories missing one.

        Returns the number of embeddings written. Intended to be called once
        on startup so older memories pick up semantic search after upgrade.
        Safe to call repeatedly — only encodes what's missing.
        """
        if not self._embedder.enabled:
            return 0
        ids = self.list_unembedded_ids(limit=batch_size)
        if not ids:
            return 0
        # Fetch title+content for each id
        placeholders = ", ".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT id, title, content FROM memories WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        texts = [self._embedding_input(r["title"], r["content"]) for r in rows]
        vectors = self._embedder.encode_many(texts)
        now = utcnow_iso()
        written = 0
        for r, vec in zip(rows, vectors, strict=False):
            if vec is None:
                continue
            try:
                self._conn.execute(
                    "INSERT INTO memory_embeddings(memory_id, model, dim, embedding, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(memory_id) DO UPDATE SET "
                    "model=excluded.model, dim=excluded.dim, embedding=excluded.embedding, "
                    "updated_at=excluded.updated_at",
                    (r["id"], self._embedder.model_name, len(vec), emb.pack(vec), now, now),
                )
                written += 1
            except Exception:
                logger.exception("backfill failed for memory %s", r["id"])
        if written:
            self._conn.commit()
            logger.info("Backfilled %d embeddings", written)
        return written
