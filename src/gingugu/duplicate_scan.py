"""Read-only near-duplicate detection - the *suggest* half of consolidation.

Nothing here writes. It finds clusters worth consolidating and hands them back
for a human (or an agent) to inspect; ``consolidation.consolidate`` is what
acts on them. Split out of ``consolidation.py`` to keep both under the
300-line limit once the write path grew its transaction handling.

Two modes:

- ``find_duplicate_clusters`` - pairwise cosine over stored embeddings.
- ``find_title_duplicate_clusters`` - exact-title fallback when a namespace
  has no embeddings at all.
"""

from __future__ import annotations

import logging
import math
import sqlite3

from . import embeddings as emb

logger = logging.getLogger(__name__)

# Suggest-mode scan bounds. The pairwise pass is O(N²) - acceptable for a
# personal namespace (hundreds), unreasonable past this cap. 0.90 was tuned on
# a real ~450-memory brain: below it, transitive union-find chains topically
# related memories (a story arc) into mega-clusters; true near-dupes sit above.
SUGGEST_MIN_SIMILARITY = 0.9
_SUGGEST_SCAN_CAP = 1000
_SUGGEST_CLUSTER_LIMIT = 10


def _cluster_pairs(
    pair_sims: dict[tuple[str, str], float],
) -> tuple[dict[str, list[str]], dict[str, float]]:
    """Union-find the above-threshold pairs into components.

    Returns ``(groups, peaks)`` keyed by component root. Nodes appear only via
    pairs, so every group has at least 2 members and peaks are computed in one
    pass instead of rescanning all pairs per cluster.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pair_sims:
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        parent[find(a)] = find(b)

    peaks: dict[str, float] = {}
    for (a, _b), sim in pair_sims.items():
        root = find(a)
        peaks[root] = max(peaks.get(root, 0.0), sim)

    groups: dict[str, list[str]] = {}
    for node in parent:
        groups.setdefault(find(node), []).append(node)
    return groups, peaks


def find_duplicate_clusters(
    conn: sqlite3.Connection,
    *,
    namespace_id: str,
    min_similarity: float = SUGGEST_MIN_SIMILARITY,
    limit: int = _SUGGEST_CLUSTER_LIMIT,
) -> dict:
    """Read-only semantic near-duplicate scan over one namespace.

    Pairwise cosine over the stored embeddings of active memories; pairs at or
    above ``min_similarity`` are union-found into clusters. Returns candidate
    clusters (ids + titles + peak similarity) for the caller to inspect and
    feed back into ``consolidate`` - nothing is written.

    Only the modal-dimension embeddings (the current model generation, same
    convention as search's dim filter) are compared: rows with no embedding
    are reported in ``skipped_no_embedding``, rows from an older model (or a
    zero vector) in ``skipped_stale_model``. Vectors are normalized once so
    each pair costs a bare dot product.
    """
    rows = conn.execute(
        "SELECT m.id, m.title, e.embedding FROM memories m "
        "LEFT JOIN memory_embeddings e ON e.memory_id = m.id "
        "WHERE m.namespace_id = ? AND m.confidence != 'deprecated'",
        (namespace_id,),
    ).fetchall()
    if len(rows) > _SUGGEST_SCAN_CAP:
        raise ValueError(
            f"namespace has {len(rows)} active memories; the O(N²) suggest scan "
            f"is capped at {_SUGGEST_SCAN_CAP}"
        )

    titles: dict[str, str] = {}
    by_dim: dict[int, dict[str, list[float]]] = {}
    no_embedding = 0
    for row in rows:
        titles[row["id"]] = row["title"]
        if row["embedding"] is None:
            no_embedding += 1
            continue
        vec = emb.unpack(row["embedding"])
        by_dim.setdefault(len(vec), {})[row["id"]] = vec

    modal = max(by_dim.values(), key=len) if by_dim else {}
    stale_model = sum(len(group) for group in by_dim.values()) - len(modal)

    unit: dict[str, list[float]] = {}
    for mid, vec in modal.items():
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            unit[mid] = [x / norm for x in vec]
        else:
            stale_model += 1  # zero vector: unusable for similarity

    members = list(unit)
    pair_sims: dict[tuple[str, str], float] = {}
    for i, a in enumerate(members):
        vec_a = unit[a]
        for b in members[i + 1 :]:
            sim = sum(x * y for x, y in zip(vec_a, unit[b], strict=True))
            if sim >= min_similarity:
                pair_sims[(a, b)] = sim

    groups, peaks = _cluster_pairs(pair_sims)
    clusters = [
        {
            "ids": group,
            "titles": [titles[mid] for mid in group],
            "similarity": round(peaks[root], 3),
        }
        for root, group in groups.items()
    ]
    clusters.sort(key=lambda c: c["similarity"], reverse=True)

    return {
        "mode": "semantic",
        "scanned": len(members),
        "skipped_no_embedding": no_embedding,
        "skipped_stale_model": stale_model,
        "clusters": clusters[:limit],
    }


def find_title_duplicate_clusters(
    conn: sqlite3.Connection, *, namespace_id: str, limit: int = _SUGGEST_CLUSTER_LIMIT
) -> dict:
    """Fallback duplicate scan when no embeddings exist: exact-title clusters."""
    rows = conn.execute(
        "SELECT title, GROUP_CONCAT(id) AS ids, COUNT(*) AS n FROM memories "
        "WHERE namespace_id = ? AND confidence != 'deprecated' "
        "GROUP BY title HAVING n > 1 ORDER BY n DESC, title ASC",
        (namespace_id,),
    ).fetchall()
    clusters = [
        {"ids": row["ids"].split(","), "titles": [row["title"]] * row["n"]} for row in rows[:limit]
    ]
    return {"mode": "title-only", "clusters": clusters}
