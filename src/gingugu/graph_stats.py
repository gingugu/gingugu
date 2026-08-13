"""Relation-graph health metrics for ``memory_stats``.

The knowledge graph is the part of gingugu that plain search cannot replace, and
until now nothing measured it. These are read-only aggregate queries: no schema
change, no mutation, no migration.

Three signals matter, and each maps to a concrete retrieval failure:

* **orphans** — a memory with no edges can only ever be found by direct search.
  Spreading activation can never wake it.
* **low-signal share** — ``related_to`` is the fallback edge type. A graph that
  is mostly ``related_to`` encodes little that the text/semantic index does not
  already infer for free.
* **over-cap memories** — spreading activation visits at most
  ``SPREAD_PER_SEED`` neighbours and does *not* rank them by relation type, so
  edges beyond that cap on a given memory are structurally unreachable. A high
  count here means edges were written that can never fire.
"""

from __future__ import annotations

import sqlite3

from .relations import SPREAD_PER_SEED

# Edge types that record direction/causality — the ones a text index cannot
# infer. Everything else (i.e. ``related_to``) is the low-signal fallback.
HIGH_SIGNAL_TYPES = ("supersedes", "contradicts", "caused_by", "parent_of", "child_of")


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


def compute_graph(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
) -> dict:
    """Relation-graph health.

    When ``namespace_id`` is given, an edge counts if **either** endpoint lives
    in that namespace (relations legitimately cross namespaces, and a
    source-only count would silently hide half of them). Orphan and degree
    counts are always over memories *in* the namespace, measured against their
    edges to anywhere.
    """
    if namespace_id:
        edge_join = (
            "FROM relations r "
            "JOIN memories sm ON sm.id = r.source_id "
            "JOIN memories tm ON tm.id = r.target_id "
            "WHERE sm.namespace_id = ? OR tm.namespace_id = ?"
        )
        edge_params: tuple = (namespace_id, namespace_id)
        mem_where = "WHERE m.namespace_id = ?"
        mem_params: tuple = (namespace_id,)
    else:
        edge_join = "FROM relations r"
        edge_params = ()
        mem_where = ""
        mem_params = ()

    edges = _scalar(conn, f"SELECT COUNT(*) {edge_join}", edge_params)

    by_relation_type = {
        row["relation_type"]: row["n"]
        for row in conn.execute(
            f"SELECT r.relation_type AS relation_type, COUNT(*) AS n {edge_join} "
            "GROUP BY r.relation_type ORDER BY n DESC",
            edge_params,
        ).fetchall()
    }

    high_signal = sum(by_relation_type.get(t, 0) for t in HIGH_SIGNAL_TYPES)

    memories = _scalar(conn, f"SELECT COUNT(*) FROM memories m {mem_where}", mem_params)

    # A memory is an orphan when no relation touches it from either side.
    orphans = _scalar(
        conn,
        f"SELECT COUNT(*) FROM memories m {mem_where} "
        f"{'AND' if mem_where else 'WHERE'} NOT EXISTS ("
        "  SELECT 1 FROM relations r WHERE r.source_id = m.id OR r.target_id = m.id"
        ")",
        mem_params,
    )

    # Memories carrying more edges than spreading activation will ever visit.
    over_cap = _scalar(
        conn,
        "SELECT COUNT(*) FROM ("
        "  SELECT m.id, ("
        "    SELECT COUNT(*) FROM relations r"
        "     WHERE r.source_id = m.id OR r.target_id = m.id"
        "  ) AS degree"
        f"  FROM memories m {mem_where}"
        ") WHERE degree > ?",
        (*mem_params, SPREAD_PER_SEED),
    )

    return {
        "edges": edges,
        "edges_per_memory": round(edges / memories, 2) if memories else 0.0,
        "by_relation_type": by_relation_type,
        "high_signal_edges": high_signal,
        "high_signal_ratio": _ratio(high_signal, edges),
        "orphans": orphans,
        "orphan_ratio": _ratio(orphans, memories),
        "over_spread_cap": over_cap,
        "spread_per_seed": SPREAD_PER_SEED,
    }
