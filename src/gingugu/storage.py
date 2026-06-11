"""CRUD operations for memories."""

from __future__ import annotations

import logging
import sqlite3
import uuid

from .models import Confidence, Memory, MemoryType, normalize_tag, utcnow_iso

logger = logging.getLogger(__name__)

_COLUMNS = (
    "id, namespace_id, type, title, content, confidence, source, "
    "created_at, updated_at, last_accessed, last_confirmed, access_count, metadata"
)


class MemoryStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

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
        self._conn.execute(
            "UPDATE memories SET title=?, content=?, confidence=?, metadata=?, "
            "updated_at=?, last_confirmed=? WHERE id=?",
            (
                title if title is not None else existing.title,
                content if content is not None else existing.content,
                new_confidence.value,
                new_metadata,
                now,
                last_confirmed,
                memory_id,
            ),
        )
        self._conn.commit()
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
