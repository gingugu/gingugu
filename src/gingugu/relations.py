"""Relationship management — turns the flat memory list into a knowledge graph.

Relations are directed edges (``source --relation_type--> target``). Traversal
is treated as undirected for surfacing purposes: a memory's neighbours include
edges where it is either the source or the target. See
docs/architecture.md → relations.

Edge creation, traversal and enumeration live here; the repair operations
(delete, retype, reverse) live in ``relation_repair.py`` and are mixed in.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from .models import CONFIDENCE_RANK, RelationType, utcnow_iso
from .relation_repair import RelationRepairMixin

logger = logging.getLogger(__name__)

# Hub-dampening budgets for 1-hop traversal (include_related extras and
# spreading activation share them). Benchmark-tuned on a real brain — see
# ``RelationManager.dampened_neighbour_ids``.
SPREAD_PER_SEED = 3
SPREAD_TOTAL = 10


class RelationManager(RelationRepairMixin):
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

    def dampened_neighbour_ids(
        self,
        seed_ids: list[str],
        *,
        per_seed: int = SPREAD_PER_SEED,
        total: int = SPREAD_TOTAL,
    ) -> list[str]:
        """Hub-dampened 1-hop neighbourhood of the seeds.

        Each seed contributes at most ``per_seed`` neighbours, chosen by
        confidence rank (desc), then **low** relation degree (a
        highly-connected "generic hub" memory carries less specific signal
        than a focused one), then recency, then id for full determinism.
        ``total`` caps the whole set, filled in seed order so the
        highest-ranked seeds' clusters win. Seeds are never included.
        Budgets are tuned against the real-brain benchmark (bench/):
        unbounded traversal averaged ~19 extras (~9.4k tokens) per
        10-seed recall on a ~530-memory brain; dampened ≤ 10.
        """
        seen = set(seed_ids)
        out: list[str] = []
        for sid in seed_ids:
            if len(out) >= total:
                break
            rows = self._conn.execute(
                "SELECT m.id, m.confidence, "
                # Freshness anchor — the LATEST of the three, matching
                # decay.reference_timestamp. created_at/updated_at are NOT NULL,
                # so only last_confirmed needs the null guard; MAX() would
                # otherwise return NULL for any NULL argument.
                "MAX(COALESCE(m.last_confirmed, ''), m.updated_at, m.created_at) AS ts, "
                "(SELECT COUNT(*) FROM relations d "
                " WHERE d.source_id = m.id OR d.target_id = m.id) AS degree "
                "FROM relations r "
                "JOIN memories m ON m.id = "
                "  CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END "
                "WHERE r.source_id = ? OR r.target_id = ?",
                (sid, sid, sid),
            ).fetchall()
            candidates = sorted(
                (
                    (
                        CONFIDENCE_RANK.get(row["confidence"], 0),
                        -row["degree"],
                        row["ts"],
                        row["id"],
                    )
                    for row in rows
                    if row["id"] not in seen
                ),
                reverse=True,
            )
            for _, _, _, other_id in candidates[:per_seed]:
                if len(out) >= total:
                    break
                seen.add(other_id)
                out.append(other_id)
        return out

    def list_edges(
        self,
        *,
        namespace_id: str | None = None,
        relation_type: RelationType | None = None,
        memory_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Enumerate edges with both endpoints resolved to titles.

        A graph you cannot read is a graph you cannot repair, and titles are what
        make an edge judgeable without a second lookup per endpoint. Filtering by
        ``namespace_id`` counts an edge if **either** endpoint lives there,
        matching ``graph_stats.compute_graph`` — relations legitimately cross
        namespaces and a source-only filter would hide half of them.

        ``degree`` per endpoint is included because it decides whether an edge can
        ever fire: past ``SPREAD_PER_SEED`` neighbours, spreading activation will
        never visit it. Ordering is deterministic (source title, then target
        title, then type) so a paged repair sweep sees each edge exactly once.
        """
        where: list[str] = []
        params: list = []
        if namespace_id:
            where.append("(sm.namespace_id = ? OR tm.namespace_id = ?)")
            params += [namespace_id, namespace_id]
        if relation_type:
            where.append("r.relation_type = ?")
            params.append(relation_type.value)
        if memory_id:
            where.append("(r.source_id = ? OR r.target_id = ?)")
            params += [memory_id, memory_id]
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        joins = (
            "FROM relations r "
            "JOIN memories sm ON sm.id = r.source_id "
            "JOIN memories tm ON tm.id = r.target_id "
            "JOIN namespaces sns ON sns.id = sm.namespace_id "
            "JOIN namespaces tns ON tns.id = tm.namespace_id "
        )
        total = int(
            self._conn.execute(f"SELECT COUNT(*) {joins}{clause}", tuple(params)).fetchone()[0]
        )

        rows = self._conn.execute(
            "SELECT r.source_id, r.target_id, r.relation_type, r.created_at, "
            "sm.title AS source_title, tm.title AS target_title, "
            "sns.name AS source_namespace, tns.name AS target_namespace, "
            "(SELECT COUNT(*) FROM relations d "
            " WHERE d.source_id = sm.id OR d.target_id = sm.id) AS source_degree, "
            "(SELECT COUNT(*) FROM relations d "
            " WHERE d.source_id = tm.id OR d.target_id = tm.id) AS target_degree "
            f"{joins}{clause} "
            "ORDER BY sm.title, tm.title, r.relation_type "
            "LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()

        return {
            "total": total,
            "returned": len(rows),
            "offset": offset,
            "edges": [dict(row) for row in rows],
        }
