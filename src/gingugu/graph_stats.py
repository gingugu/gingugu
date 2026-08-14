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

from .models import CONFIDENCE_RANK
from .relations import SPREAD_PER_SEED

# Edge types that record direction/causality — the ones a text index cannot
# infer. Everything else (i.e. ``related_to``) is the low-signal fallback.
HIGH_SIGNAL_TYPES = ("supersedes", "contradicts", "caused_by", "parent_of", "child_of")

# Mirrors the review/claim sample caps: the full count is always reported, and
# a sweep raises the sample (via ``memory_stats(review_limit=...)``) to
# enumerate the whole backlog.
_SAMPLE_LIMIT = 5
_SAMPLE_MAX = 100

# Rank confidences in SQL without a join, so the sample can be ordered by the
# same scale the rest of the codebase uses.
_CONFIDENCE_CASE = (
    "CASE m.confidence "
    + " ".join(f"WHEN '{name}' THEN {rank}" for name, rank in CONFIDENCE_RANK.items())
    + " ELSE 0 END"
)


def orphan_filter(alias: str = "m") -> str:
    """WHERE fragment keeping only memories no relation touches, either way.

    Takes no parameters — the predicate is pure structure — so it composes with
    the FTS5 join, the embeddings join, and a plain table scan alike, exactly
    like ``claim_queries.claim_filter``. Shared by the ``orphans`` metric here
    and ``memory_search(orphans=True)``: a count and its enumeration must agree
    on what they are counting, and two hand-written copies of the same
    correlated subquery is how they stop agreeing.
    """
    return (
        f"NOT EXISTS (SELECT 1 FROM relations r "
        f"WHERE r.source_id = {alias}.id OR r.target_id = {alias}.id)"
    )


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


def _orphan_sample(
    conn: sqlite3.Connection,
    *,
    mem_where: str,
    mem_params: tuple,
    limit: int,
) -> list[dict]:
    """The orphans most worth reconnecting, best first.

    Ordered by confidence, then access count, then recency: a verified memory
    that gets recalled often and is still cut out of the graph is costing the
    most retrieval, because spreading activation can never reach it. Deprecated
    orphans sink to the bottom rather than being filtered out, so the sample is
    drawn from exactly the population ``orphans`` counts — no second number is
    needed to explain a gap between the count and the list.

    Each row carries its ``namespace``, since a store-wide call spans all of
    them and "which namespace are these in" is the first question a
    reconnection sweep asks.
    """
    rows = conn.execute(
        "SELECT m.id, m.type, m.title, m.confidence, m.access_count, n.name AS namespace "
        "FROM memories m JOIN namespaces n ON n.id = m.namespace_id "
        f"{mem_where} {'AND' if mem_where else 'WHERE'} {orphan_filter('m')} "
        f"ORDER BY {_CONFIDENCE_CASE} DESC, m.access_count DESC, "
        "MAX(COALESCE(m.last_confirmed, ''), m.updated_at, m.created_at) DESC "
        "LIMIT ?",
        (*mem_params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def compute_graph(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
    sample_limit: int | None = None,
) -> dict:
    """Relation-graph health.

    When ``namespace_id`` is given, an edge counts if **either** endpoint lives
    in that namespace (relations legitimately cross namespaces, and a
    source-only count would silently hide half of them). Orphan and degree
    counts are always over memories *in* the namespace, measured against their
    edges to anywhere.

    ``orphan_sample`` names the orphans the count reports. A count on its own
    describes a backlog nothing can work through: knowing 45 memories are cut
    out of the graph does not identify one of them, and reconnecting them meant
    querying the database behind the server's back. ``sample_limit`` raises the
    list (max ``_SAMPLE_MAX``) so a sweep can enumerate every one.
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
        f"{'AND' if mem_where else 'WHERE'} {orphan_filter('m')}",
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
        "orphan_sample": _orphan_sample(
            conn,
            mem_where=mem_where,
            mem_params=mem_params,
            limit=max(1, min(_SAMPLE_LIMIT if sample_limit is None else sample_limit, _SAMPLE_MAX)),
        ),
        "over_spread_cap": over_cap,
        "spread_per_seed": SPREAD_PER_SEED,
    }
