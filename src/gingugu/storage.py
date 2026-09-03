"""CRUD operations for the ``memories`` row itself.

``MemoryStore`` owns that row and the transaction boundary around it. The four
satellite tables a memory drags along - tags, access log, embeddings, claims -
are reached through ``DerivedTables``, which carries the whole delegation
surface and explains why each one lives in its own module.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from . import claim_sync
from .embeddings import EmbeddingProvider, NullEmbeddingProvider
from .models import (
    Confidence,
    Memory,
    MemoryType,
    memory_columns_sql,
    memory_placeholders_sql,
    normalize_metadata,
    utcnow_iso,
)
from .storage_derived import DerivedTables
from .transactions import TransactionParticipant

logger = logging.getLogger(__name__)

_COLUMNS = memory_columns_sql()


class MemoryStore(DerivedTables, TransactionParticipant):
    def __init__(
        self,
        conn: sqlite3.Connection,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        super().__init__()
        self._conn = conn
        self._embedder = embedder or NullEmbeddingProvider()

    @property
    def embedder(self) -> EmbeddingProvider:
        return self._embedder

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
        metadata = normalize_metadata(metadata)
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
        self._commit()
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
            # normalize_metadata returns None for "" and validates JSON-object shape
            # for everything else (raising ValueError on bad input).
            new_metadata = normalize_metadata(metadata)
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
        self._commit()
        # Re-encode only when the text the embedding was derived from actually
        # changed — confidence/metadata updates don't invalidate the vector.
        if text_changed:
            self._persist_embedding(memory_id, new_title, new_content)
        return self.get(memory_id, record_access=False)

    def delete(self, memory_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._prune_orphan_tags()
        self._commit()
        return cur.rowcount > 0

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
