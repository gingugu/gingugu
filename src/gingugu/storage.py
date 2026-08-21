"""CRUD operations for memories."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from . import claim_sync, embedding_sync
from . import embeddings as emb
from .embeddings import EmbeddingProvider, NullEmbeddingProvider
from .models import (
    Confidence,
    Memory,
    MemoryType,
    memory_columns_sql,
    memory_placeholders_sql,
    normalize_tag,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

_COLUMNS = memory_columns_sql()


def _normalize_metadata(metadata: str | None) -> str | None:
    """Validate and canonicalize a metadata payload.

    The schema treats ``metadata`` as a JSON blob, so we enforce that on
    write rather than letting arbitrary strings accumulate. Rules:

    - ``None`` → ``None`` (unchanged).
    - ``""`` → ``None`` (caller convention: empty string clears metadata).
    - Otherwise must parse as a JSON **object** (``{...}``); arrays,
      numbers, strings, booleans, and ``null`` are rejected. Object form
      is what every existing callsite assumes and what future provenance
      fields (``created_by``, ``client``, ``evidence``, …) plug into.
    - Valid input is re-serialized with sorted keys so equivalent payloads
      are stored identically (helps deduplication and diffs).

    Raises ``ValueError`` on invalid JSON or wrong shape.
    """
    if metadata is None:
        return None
    if metadata == "":
        return None
    try:
        parsed = json.loads(metadata)
    except json.JSONDecodeError as e:
        raise ValueError(f"metadata must be valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"metadata must be a JSON object, got {type(parsed).__name__}")
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False)


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
        metadata = _normalize_metadata(metadata)
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
            f"INSERT INTO memories({_COLUMNS}) VALUES ({memory_placeholders_sql()})",
            {
                **mem.model_dump(exclude={"score", "tags"}),
                "type": mem.type.value,
                "confidence": mem.confidence.value,
                # New memories are never born pinned: pinning is a deliberate,
                # budgeted decision made after the fact, never a store-time default.
                "pinned": 0,
            },
        )
        if tags:
            self.set_tags(mem.id, tags, commit=False)
        claim_sync.sync(self._conn, mem, now)
        self._conn.commit()
        mem.tags = self.get_tags(mem.id)
        self._persist_embedding(mem.id, mem.title, mem.content)
        logger.info("Stored memory %s (%s)", mem.id, mem.title)
        return mem

    def contradicted_memories(self, mem: Memory) -> list[dict]:
        """Older memories whose open state claim ``mem`` has just resolved.

        Advisory only — nothing is mutated. See ``claim_sync.contradicted``.
        """
        return claim_sync.contradicted(self._conn, mem)

    def resolve_claims(
        self, memory_id: str, refs: list[str], *, resolved_by: str | None = None
    ) -> list[str]:
        """Mark open claims resolved without touching the memory's prose."""
        return claim_sync.resolve(
            self._conn, memory_id=memory_id, refs=refs, resolved_by=resolved_by
        )

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
        type: MemoryType | None = None,
        confidence: Confidence | None = None,
        metadata: str | None = None,
        pinned: bool | None = None,
    ) -> Memory | None:
        existing = self.get(memory_id, record_access=False)
        if existing is None:
            return None
        now = utcnow_iso()
        new_type = type or existing.type
        new_confidence = confidence or existing.confidence
        new_title = title if title is not None else existing.title
        new_content = content if content is not None else existing.content
        # Rewriting the title or content IS a confirmation: someone re-read the
        # claim and restated it. Tag-only, retype-only, confidence-only and
        # metadata-only edits assert nothing about truth and must not advance
        # the clock — the same "did the matching surface move?" test
        # memory_update already applies to relation hints. Without this, routine
        # content maintenance never registered and the freshness signal rotted.
        # Accepted trade: a one-word typo fix also resets the staleness clock,
        # suppressing review hints and suggests_deprecation.
        text_changed = new_title != existing.title or new_content != existing.content
        last_confirmed = existing.last_confirmed
        if confidence == Confidence.VERIFIED or text_changed:
            last_confirmed = now
        # Empty string clears metadata to NULL (None means "leave unchanged" —
        # MCP optional params cannot distinguish absent from null).
        if metadata is None:
            new_metadata = existing.metadata
        else:
            # _normalize_metadata returns None for "" and validates JSON-object shape
            # for everything else (raising ValueError on bad input).
            new_metadata = _normalize_metadata(metadata)
        # Pinning is a retrieval-priority decision, not a claim about truth, so
        # it deliberately does not advance last_confirmed (same reasoning as a
        # metadata-only edit above).
        new_pinned = existing.pinned if pinned is None else pinned
        self._conn.execute(
            "UPDATE memories SET title=?, content=?, type=?, confidence=?, metadata=?, "
            "pinned=?, updated_at=?, last_confirmed=? WHERE id=?",
            (
                new_title,
                new_content,
                new_type.value,
                new_confidence.value,
                new_metadata,
                int(new_pinned),
                now,
                last_confirmed,
                memory_id,
            ),
        )
        # Claims are derived from the text, so they only need re-deriving when
        # the text moved. Done before the commit so both land atomically.
        if text_changed:
            existing.title, existing.content = new_title, new_content
            claim_sync.sync(self._conn, existing, now)
        self._conn.commit()
        # Re-encode only when the text the embedding was derived from actually
        # changed — confidence/metadata updates don't invalidate the vector.
        if text_changed:
            self._persist_embedding(memory_id, new_title, new_content)
        return self.get(memory_id, record_access=False)

    def delete(self, memory_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._prune_orphan_tags()
        self._conn.commit()
        return cur.rowcount > 0

    def _record_access(self, memory_id: str) -> None:
        self.record_accesses([memory_id])

    def record_accesses(self, memory_ids: list[str]) -> int:
        """Log a real access against each memory: bumps ``access_count``,
        refreshes ``last_accessed``, and writes an ``access_log`` row.

        This is the bulk primitive used by retrieval handlers (recall, search,
        context) to credit the seeds they actually returned. Spreading-activation
        neighbours go through ``touch_many`` instead — that path refreshes the
        dormancy clock without inflating access counts. Returns the number of
        memories whose row was updated.
        """
        ids = list(dict.fromkeys(mid for mid in memory_ids if mid))
        if not ids:
            return 0
        now = utcnow_iso()
        self._conn.executemany(
            "INSERT INTO access_log(id, memory_id, accessed_at) VALUES (?, ?, ?)",
            [(str(uuid.uuid4()), mid, now) for mid in ids],
        )
        placeholders = ", ".join("?" for _ in ids)
        cur = self._conn.execute(
            f"UPDATE memories SET access_count = access_count + 1, last_accessed = ? "
            f"WHERE id IN ({placeholders})",
            (now, *ids),
        )
        self._conn.commit()
        return cur.rowcount

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

    def count_pinned(self, namespace_id: str) -> int:
        """Active pins in a namespace. Mirrors the filter ``context._pinned``
        loads with, so the cap is enforced against what actually surfaces —
        deprecated pins are inert and must not consume the budget."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM memories "
            "WHERE namespace_id = ? AND pinned = 1 AND confidence != 'deprecated'",
            (namespace_id,),
        ).fetchone()
        return int(row[0]) if row else 0

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
    #
    # Thin delegations to `embedding_sync`, which owns the
    # memories -> memory_embeddings invariant. It lives outside this class
    # because `MemoryStore` is not the only writer of memory rows:
    # `memory_import` writes them too, and while this logic was private to the
    # store it had no way to honor the invariant.

    _embedding_input = staticmethod(emb.embedding_input)

    def _persist_embedding(self, memory_id: str, title: str, content: str) -> None:
        embedding_sync.persist_one(self._conn, self._embedder, memory_id, title, content)

    def get_embedding(self, memory_id: str) -> list[float] | None:
        """The stored vector for a memory, or None if absent or from another model."""
        return embedding_sync.get_one(self._conn, self._embedder, memory_id)

    def get_embeddings_for(self, memory_ids: list[str]) -> dict[str, list[float]]:
        """Bulk fetch embeddings keyed by memory_id."""
        return embedding_sync.get_many(self._conn, self._embedder, memory_ids)

    def list_unembedded_ids(self, *, limit: int = 100) -> list[str]:
        """IDs of memories without a current-model embedding (for backfill)."""
        return embedding_sync.unembedded_ids(self._conn, self._embedder, limit=limit)

    def embed_memories(self, memory_ids: list[str], *, batch_size: int = 32) -> int:
        """Encode and persist vectors for every named memory. Returns rows written."""
        return embedding_sync.embed_ids(
            self._conn, self._embedder, memory_ids, batch_size=batch_size
        )

    def backfill_embeddings(self, *, batch_size: int = 32) -> int:
        """Encode one batch of memories missing a current-model embedding.

        Called once on startup so rows predating an embedding upgrade recover.
        Safe to call repeatedly. Bulk importers should use `embed_memories`,
        which finishes the job instead of draining one batch per process.
        """
        return embedding_sync.backfill(self._conn, self._embedder, batch_size=batch_size)
