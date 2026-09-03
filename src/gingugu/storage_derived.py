"""Everything that hangs off a memory row, as one delegation surface.

A memory is one row in ``memories`` plus four satellite tables that must stay
in step with it: ``memory_tags``, ``access_log``, ``memory_embeddings`` and
``memory_claims``. Each of those is owned by its own module over a bare
connection - ``tags``, ``access``, ``embedding_sync``, ``claim_sync`` - because
``MemoryStore`` is not the only writer of memory rows and an invariant locked
inside that class is one ``memory_import`` has no way to honor.

What is left over is the pass-through surface callers actually hold, and this
is it. Keeping it here means ``storage`` reads as the row's own CRUD, and the
question "what else does writing a memory touch?" has one answer in one file.

Mixed into ``MemoryStore`` alongside ``TransactionParticipant``, which supplies
``_commit`` and ``_after_commit``. Everything this mixin needs from the
concrete class is declared below under ``TYPE_CHECKING`` and never defined at
runtime - a real stub here would sit ahead of ``TransactionParticipant`` in the
MRO and shadow the implementation it is supposed to be borrowing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import access, claim_sync, embedding_sync
from . import tags as tags_mod
from .embeddings import EmbeddingProvider
from .models import Memory
from .session import current_session_id


class DerivedTables:
    """Delegations to the modules owning a memory's satellite tables."""

    if TYPE_CHECKING:  # supplied by MemoryStore / TransactionParticipant
        _conn: sqlite3.Connection
        _embedder: EmbeddingProvider

        def _commit(self) -> None: ...

        def _after_commit(self, fn: Callable[[], None]) -> None: ...

    # --- Claims --------------------------------------------------------------

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

    # --- Access accounting ---------------------------------------------------
    #
    # Both return early on an empty id list WITHOUT committing: inside an
    # `atomic()` block a stray commit would close a transaction the caller
    # still owns.

    def _record_access(self, memory_id: str) -> None:
        self.record_accesses([memory_id])

    def record_accesses(self, memory_ids: list[str]) -> int:
        """Credit a real access to each memory. Returns rows updated.

        The bulk primitive retrieval handlers (recall, search, context) use to
        credit the seeds they actually returned. See ``access.record``.
        """
        ids = access.dedupe(memory_ids)
        if not ids:
            return 0
        updated = access.record(self._conn, ids, session_id=current_session_id())
        self._commit()
        return updated

    def touch_many(self, memory_ids: list[str]) -> int:
        """Reactivate memories without counting an access. Returns rows updated.

        The spreading-activation primitive: neighbours of a recalled memory get
        their dormancy clock reset, but nobody asked for them, so
        ``access_count`` is untouched. See ``access.touch``.
        """
        ids = access.dedupe(memory_ids)
        if not ids:
            return 0
        updated = access.touch(self._conn, ids)
        self._commit()
        return updated

    # --- Tags ----------------------------------------------------------------

    def set_tags(self, memory_id: str, tags: list[str], *, commit: bool = True) -> list[str]:
        """Replace all tags on a memory with the normalized, de-duplicated set."""
        normalized = tags_mod.set_for(self._conn, memory_id, tags)
        if commit:
            self._commit()
        return normalized

    def add_tags(self, memory_id: str, tags: list[str], *, commit: bool = True) -> list[str]:
        """Add tags to a memory without removing existing ones."""
        tags_mod.add_to(self._conn, memory_id, tags)
        if commit:
            self._commit()
        return self.get_tags(memory_id)

    def get_tags(self, memory_id: str) -> list[str]:
        return tags_mod.get_for(self._conn, memory_id)

    def load_tags(self, memories: list[Memory]) -> None:
        """Batch-populate ``.tags`` on a list of Memory objects."""
        for mem in memories:
            mem.tags = self.get_tags(mem.id)

    def _prune_orphan_tags(self) -> None:
        tags_mod.prune_orphans(self._conn)

    # --- Embeddings ----------------------------------------------------------

    def _persist_embedding(self, memory_id: str, title: str, content: str) -> None:
        # Deferred inside an `atomic()` block: the vector write commits on its
        # own, and a row whose memory the block later rolls back is an orphan.
        # See transactions.py → deferred side effects.
        self._after_commit(
            lambda: embedding_sync.persist_one(
                self._conn, self._embedder, memory_id, title, content
            )
        )

    def get_embedding(self, memory_id: str) -> list[float] | None:
        """The stored vector for a memory, or None if absent or from another model."""
        return embedding_sync.get_one(self._conn, self._embedder, memory_id)

    def get_embeddings_for(self, memory_ids: list[str]) -> dict[str, list[float]]:
        """Bulk fetch embeddings keyed by memory_id."""
        return embedding_sync.get_many(self._conn, self._embedder, memory_ids)

    def backfill_embeddings(self, *, batch_size: int = 32) -> int:
        """Encode one batch of memories missing a current-model embedding.

        Called once on startup so rows predating an embedding upgrade recover.
        Safe to call repeatedly. A bulk importer that needs the job finished
        rather than one batch drained should call ``embedding_sync.embed_ids``
        directly.
        """
        return embedding_sync.backfill(self._conn, self._embedder, batch_size=batch_size)
