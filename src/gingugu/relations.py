"""Relationship management — turns the flat memory list into a knowledge graph.

Relations are directed edges (``source --relation_type--> target``). Traversal
is treated as undirected for surfacing purposes: a memory's neighbours include
edges where it is either the source or the target. See
docs/architecture.md → relations.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from .models import RelationType, utcnow_iso

logger = logging.getLogger(__name__)


class RelationManager:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def _exists(self, memory_id: str) -> bool:
        return (
            self._conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone()
            is not None
        )

    def relate(
        self,
        *,
        source_id: str,
        target_id: str,
        relation_type: RelationType,
        metadata: str | None = None,
    ) -> dict:
        """Create a directed relation. Idempotent on (source, target, type)."""
        if source_id == target_id:
            raise ValueError("a memory cannot relate to itself")
        if not self._exists(source_id):
            raise ValueError(f"source memory {source_id!r} not found")
        if not self._exists(target_id):
            raise ValueError(f"target memory {target_id!r} not found")

        relation_id = str(uuid.uuid4())
        now = utcnow_iso()
        self._conn.execute(
            "INSERT INTO relations(id, source_id, target_id, relation_type, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_id, target_id, relation_type) DO NOTHING",
            (relation_id, source_id, target_id, relation_type.value, now, metadata),
        )
        self._conn.commit()
        return {
            "source_id": source_id,
            "target_id": target_id,
            "relation_type": relation_type.value,
        }

    def get_relations(self, memory_id: str) -> list[dict]:
        """All edges touching this memory, with direction relative to it."""
        rows = self._conn.execute(
            "SELECT id, source_id, target_id, relation_type, created_at, metadata "
            "FROM relations WHERE source_id = ? OR target_id = ? "
            "ORDER BY created_at",
            (memory_id, memory_id),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            outgoing = r["source_id"] == memory_id
            out.append(
                {
                    "relation_type": r["relation_type"],
                    "direction": "outgoing" if outgoing else "incoming",
                    "other_id": r["target_id"] if outgoing else r["source_id"],
                }
            )
        return out

    def related_ids(self, memory_id: str) -> list[str]:
        """Neighbour memory ids (both directions), de-duplicated, order-stable."""
        return list(dict.fromkeys(rel["other_id"] for rel in self.get_relations(memory_id)))

    def delete_relation(
        self, *, source_id: str, target_id: str, relation_type: RelationType
    ) -> bool:
        cur = self._conn.execute(
            "DELETE FROM relations WHERE source_id = ? AND target_id = ? AND relation_type = ?",
            (source_id, target_id, relation_type.value),
        )
        self._conn.commit()
        return cur.rowcount > 0
