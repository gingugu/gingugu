"""Orphan reconnection - finding the memories the graph cannot reach.

An orphan is a memory no relation touches. It is still searchable, so it does
not feel lost, but spreading activation can never wake it: it will only ever
surface when someone happens to query near it directly. Every orphan is a
memory that has quietly stopped participating in recall, and a real brain
accumulates them steadily - they are the default state of anything written in a
hurry.

The pass proposes **which two memories to connect and how close they measure**.
It does not propose the relation type, and that restraint is not politeness. A
similarity score says these two texts are about the same thing; it contains
nothing about whether one supersedes the other, caused the other, or is the
parent of it. Choosing among those is a claim about what happened, and only a
person holds it. Math finds the pair; judgment types the edge.

Two stages, matching the write-time hints exactly:

1. **Retrieval** narrows thousands of memories to a handful of candidates.
   Its fused rank is excellent for that and means nothing as a magnitude.
2. **Adjudication** rescores those candidates absolutely with
   ``payload_similarity`` and applies the calibrated ``RELATION_MIN_SIMILARITY``
   floor - the same cutoff, measured on the same corpus, that gates a relation
   suggestion at write time. A proposal and a hint disagreeing about what
   "close enough to relate" means would be two answers to one question.
"""

from __future__ import annotations

import logging
import sqlite3

from .. import search as search_mod
from ..embeddings import EmbeddingProvider
from ..similarity import RELATION_MIN_SIMILARITY, payload_similarity
from .graph import Graph

logger = logging.getLogger(__name__)

# Orphans examined per run. The backlog is worked down over successive runs
# rather than dumped in one queue nobody reads: a review list of two hundred
# untyped pairs is a list that stays untouched, and the pass runs again
# tomorrow regardless.
ORPHAN_LIMIT = 25

# Candidates retrieved per orphan before absolute rescoring. Wide enough that
# the real neighbour is in the pool, narrow enough that a run stays cheap.
CANDIDATE_POOL = 20


def _best_match(
    conn: sqlite3.Connection,
    embedder: EmbeddingProvider | None,
    *,
    orphan_id: str,
    namespace_id: str,
    title: str,
    content: str,
) -> dict | None:
    """The closest memory to this orphan, or None if nothing clears the floor.

    Returning None is the expected outcome for a genuinely isolated memory, and
    it is the right one. An orphan with no close neighbour should stay an
    orphan rather than be wired to whatever ranked highest - a graph padded
    with edges that mean nothing is worse for retrieval than a graph with a
    hole in it, because spreading activation will then spend a seed's budget
    on them.
    """
    query = f"{title} {content}".strip()
    if not query:
        return None

    try:
        hits = search_mod.search(
            conn,
            query=query,
            namespace_id=namespace_id,
            limit=CANDIDATE_POOL,
            embedder=embedder,
        )
    except Exception:  # pragma: no cover - defensive; a pass must never crash
        logger.warning("orphan candidate retrieval failed for %s", orphan_id, exc_info=True)
        return None

    # The orphan retrieves itself, every time, at the top.
    candidates = [m for m in hits if m.id != orphan_id]
    if not candidates:
        return None

    try:
        scores, basis = payload_similarity(
            conn,
            embedder,
            title=title,
            content=content,
            memory_ids=[m.id for m in candidates],
        )
    except Exception:  # pragma: no cover - defensive
        logger.warning("orphan adjudication failed for %s", orphan_id, exc_info=True)
        return None

    floor = RELATION_MIN_SIMILARITY[basis]
    ranked = sorted(candidates, key=lambda m: -scores.get(m.id, 0.0))
    best = ranked[0]
    similarity = scores.get(best.id, 0.0)
    if similarity < floor:
        return None

    return {"memory": best, "similarity": similarity, "basis": basis, "floor": floor}


def find(
    conn: sqlite3.Connection,
    graph: Graph,
    *,
    embedder: EmbeddingProvider | None = None,
) -> list[dict]:
    """Reconnection candidates for the orphans in scope, closest first."""
    orphan_ids = graph.orphans[:ORPHAN_LIMIT]
    if not orphan_ids:
        return []

    placeholders = ", ".join("?" for _ in orphan_ids)
    rows = conn.execute(
        f"SELECT id, namespace_id, title, content FROM memories WHERE id IN ({placeholders})",
        orphan_ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}

    findings = []
    for orphan_id in orphan_ids:  # graph order, so a run is reproducible
        row = by_id.get(orphan_id)
        if row is None:  # pragma: no cover - forgotten mid-run
            continue
        match = _best_match(
            conn,
            embedder,
            orphan_id=orphan_id,
            namespace_id=row["namespace_id"],
            title=row["title"],
            content=row["content"],
        )
        if match is None:
            continue

        findings.append(
            {
                "subject_id": orphan_id,
                "object_id": match["memory"].id,
                "score": round(match["similarity"], 4),
                "evidence": {
                    "similarity": round(match["similarity"], 4),
                    "basis": match["basis"],
                    "cutoff": match["floor"],
                    "orphan_title": row["title"],
                    "candidate_title": match["memory"].title,
                    "candidate_degree": graph.degree(match["memory"].id),
                    # Stated so a reviewer knows what is missing rather than
                    # having to notice: the pass measured closeness and stopped.
                    "relation_type": None,
                },
            }
        )

    findings.sort(key=lambda f: (-f["score"], f["subject_id"]))
    return findings
