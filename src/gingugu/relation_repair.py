"""Graph repair operations — delete, retype, and reverse existing edges.

Split from ``relations.py`` (which owns edge creation, traversal and
enumeration) to keep both modules inside the repo's size discipline, and
because the two halves answer different questions: that module is how the
graph is built and read, this one is how a wrong edge is put right.

Every repair here is an UPDATE or DELETE on the existing row rather than a
delete-and-recreate, so an edge's id, ``created_at`` and ``metadata`` survive
being relabelled or turned around. The graph should record when a link was
first drawn, not when it was last corrected.

Mixed into ``RelationManager``; it uses only ``self._conn``.
"""

from __future__ import annotations

import sqlite3

from .models import RelationType


class RelationRepairMixin:
    """Delete/retype/reverse operations over the ``relations`` table."""

    _conn: sqlite3.Connection

    def delete_relation(
        self, *, source_id: str, target_id: str, relation_type: RelationType
    ) -> bool:
        cur = self._conn.execute(
            "DELETE FROM relations WHERE source_id = ? AND target_id = ? AND relation_type = ?",
            (source_id, target_id, relation_type.value),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_edges(self, *, source_id: str, target_id: str) -> list[str]:
        """Delete every edge in this direction, whatever its type. Returns the types removed."""
        types = [
            row["relation_type"]
            for row in self._conn.execute(
                "SELECT relation_type FROM relations WHERE source_id = ? AND target_id = ? "
                "ORDER BY relation_type",
                (source_id, target_id),
            ).fetchall()
        ]
        if types:
            self._conn.execute(
                "DELETE FROM relations WHERE source_id = ? AND target_id = ?",
                (source_id, target_id),
            )
            self._conn.commit()
        return types

    def retype_relation(
        self,
        *,
        source_id: str,
        target_id: str,
        old_type: RelationType,
        new_type: RelationType,
    ) -> str:
        """Relabel an existing edge in place, preserving direction and provenance.

        The common repair is "right connection, wrong label", so the row is
        UPDATEd rather than recreated: its id, ``created_at`` and ``metadata``
        survive, and the graph keeps an honest record of when the link was
        first drawn. Returns one of:

        * ``retyped`` — the label was changed.
        * ``merged`` — an edge of ``new_type`` already joined this pair, so the
          old row was dropped into it. Reported distinctly because the edge
          count falls by one; nothing is invented to keep the arithmetic tidy.
        * ``unchanged`` — ``old_type`` and ``new_type`` are the same.
        * ``not_found`` — no such edge to repair.
        """
        if old_type == new_type:
            return "unchanged" if self._edge_exists(source_id, target_id, old_type) else "not_found"
        if not self._edge_exists(source_id, target_id, old_type):
            return "not_found"

        if self._edge_exists(source_id, target_id, new_type):
            self._conn.execute(
                "DELETE FROM relations WHERE source_id = ? AND target_id = ? AND relation_type = ?",
                (source_id, target_id, old_type.value),
            )
            self._conn.commit()
            return "merged"

        self._conn.execute(
            "UPDATE relations SET relation_type = ? "
            "WHERE source_id = ? AND target_id = ? AND relation_type = ?",
            (new_type.value, source_id, target_id, old_type.value),
        )
        self._conn.commit()
        return "retyped"

    def reverse_relation(
        self,
        *,
        source_id: str,
        target_id: str,
        relation_type: RelationType,
        new_type: RelationType | None = None,
    ) -> str:
        """Swap an edge's endpoints in place, optionally relabelling it too.

        The sibling repair to ``retype_relation``: "right pair, wrong
        direction". Both are one UPDATE on the existing row, so id,
        ``created_at`` and ``metadata`` survive and the graph keeps an honest
        record of when the link was first drawn. Doing it as delete +
        ``memory_relate`` would reset all three and cost two calls per edge.

        ``new_type`` reverses and retypes in a single write, because an edge
        recorded backwards is frequently mislabelled as well — whoever wrote
        ``A caused_by B`` for ``B caused_by A`` was not reading the direction
        closely. Note that reversing ``parent_of``/``child_of`` is the same
        operation as flipping between them: pick whichever reads better and
        do not do both, or you land back where you started.

        Returns one of:

        * ``reversed`` — the endpoints (and type, if given) were swapped.
        * ``merged`` — the reversed edge already existed, so the original row
          was dropped into it. Reported distinctly because the edge count falls
          by one; nothing is invented to keep the arithmetic tidy.
        * ``not_found`` — no such edge to repair.
        """
        if not self._edge_exists(source_id, target_id, relation_type):
            return "not_found"

        final_type = new_type or relation_type
        if self._edge_exists(target_id, source_id, final_type):
            self._conn.execute(
                "DELETE FROM relations WHERE source_id = ? AND target_id = ? AND relation_type = ?",
                (source_id, target_id, relation_type.value),
            )
            self._conn.commit()
            return "merged"

        self._conn.execute(
            "UPDATE relations SET source_id = ?, target_id = ?, relation_type = ? "
            "WHERE source_id = ? AND target_id = ? AND relation_type = ?",
            (target_id, source_id, final_type.value, source_id, target_id, relation_type.value),
        )
        self._conn.commit()
        return "reversed"

    def _edge_exists(self, source_id: str, target_id: str, relation_type: RelationType) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM relations "
                "WHERE source_id = ? AND target_id = ? AND relation_type = ?",
                (source_id, target_id, relation_type.value),
            ).fetchone()
            is not None
        )
