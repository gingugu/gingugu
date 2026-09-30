"""``memory_stats.hygiene`` — cheap, SQL-only cleanup signals.

Split out of ``stats.py`` to keep that module under the repo's size limit,
mirroring how ``graph_stats.py`` and ``size_stats.py`` already carry their own
``memory_stats`` sub-block.
"""

from __future__ import annotations

import sqlite3

# Cap on how many duplicate-title clusters we surface in the stats sample.
# The full count is always reported; the sample is just for human inspection.
_HYGIENE_SAMPLE_LIMIT = 5


def compute_hygiene(conn: sqlite3.Connection, *, namespace_id: str | None = None) -> dict:
    """Cheap, SQL-only hygiene signals for catching cleanup candidates.

    Surfaces three things the manual namespace-scan workflow looks for first:

    * ``ghost_namespaces`` — namespaces with zero memories (skipped when a
      ``namespace_id`` filter is applied, since the scope is a single ns).
    * ``duplicate_title_count`` — number of (namespace, title) pairs that
      appear in 2+ active memories. A strong signal of literal duplication.
    * ``duplicate_title_sample`` — up to ``_HYGIENE_SAMPLE_LIMIT`` of those
      clusters with their memory ids, so the caller can inspect or merge.

    Semantic near-duplicate detection is intentionally NOT done here — the
    N² comparisons would be too expensive for a stats call. Stores get a
    semantic hint via ``memory_store``'s ``similar_memories``.
    """
    ghost_namespaces: list[str] = []
    if namespace_id is None:
        ghost_namespaces = [
            row["name"]
            for row in conn.execute(
                "SELECT n.name AS name FROM namespaces n "
                "LEFT JOIN memories m ON m.namespace_id = n.id "
                "GROUP BY n.id HAVING COUNT(m.id) = 0 ORDER BY n.name"
            ).fetchall()
        ]

    and_ns = " AND namespace_id = ?" if namespace_id else ""
    ns_params: tuple = (namespace_id,) if namespace_id else ()

    clusters = conn.execute(
        "SELECT namespace_id, title, GROUP_CONCAT(id) AS ids, COUNT(*) AS n "
        "FROM memories WHERE confidence != 'deprecated'" + and_ns + " "
        "GROUP BY namespace_id, title HAVING n > 1 "
        "ORDER BY n DESC, title ASC",
        ns_params,
    ).fetchall()

    ns_names = {
        row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM namespaces").fetchall()
    }

    sample = [
        {
            "namespace": ns_names.get(row["namespace_id"], "?"),
            "title": row["title"],
            "ids": row["ids"].split(","),
        }
        for row in clusters[:_HYGIENE_SAMPLE_LIMIT]
    ]

    return {
        "ghost_namespaces": ghost_namespaces,
        "duplicate_title_count": len(clusters),
        "duplicate_title_sample": sample,
    }
