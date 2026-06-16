"""Auto-context generation for session start (``memory_context``).

The result set draws from three intent buckets, each ranked by its *own*
native signal and given a guaranteed quota of the ``limit`` slots, so no one
intent can be starved by the composite-score ranking (see
docs/architecture.md → memory_context):

1. Task-relevant (if ``task_hint``) — FTS5 search scoped to namespace,
   ranked by composite relevance.
2. Recently active in this namespace — by ``last_accessed`` (pure recency),
   excluding deprecated.
3. Cross-namespace high-confidence patterns — pattern/preference + verified,
   ranked by ``access_count``.

Quotas are filled recency-first so a freshly-stored, never-accessed memory
(the "where we left off" signal) always survives the cut. Any slots left after
the guaranteed quotas are backfilled from the combined pool by composite score.

Types ``architecture`` and ``decision`` get a +0.1 score boost (disproportionately
useful at session start).
"""

from __future__ import annotations

import math
import sqlite3

from . import decay, search
from .embeddings import EmbeddingProvider
from .models import Memory

_BOOST_TYPES = {"architecture", "decision"}
_BOOST_AMOUNT = 0.1

# Guaranteed share of the result ``limit`` reserved for each intent bucket.
# Recency is filled first (it's the intent the old score-and-collapse design
# starved); task-relevance is the primary intent when a hint is given;
# cross-namespace wisdom yields first when slots are contended.
_TASK_RATIO = 0.5
_RECENT_RATIO = 0.3
_CROSS_NS_QUOTA = 3

_COLUMNS = (
    "id, namespace_id, type, title, content, confidence, source, "
    "created_at, updated_at, last_accessed, last_confirmed, access_count, metadata"
)


def _score(mem: Memory, weights: dict[str, float], decay_lambda: float, relevance: float) -> float:
    """Composite score without the type boost (applied once, later, for all buckets)."""
    return decay.score_memory(
        relevance=relevance,
        last_confirmed=mem.last_confirmed,
        updated_at=mem.updated_at,
        created_at=mem.created_at,
        access_count=mem.access_count,
        confidence=mem.confidence.value,
        weights=weights,
        decay_lambda=decay_lambda,
    )


def _recently_active(conn: sqlite3.Connection, namespace_id: str, limit: int) -> list[Memory]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM memories "
        "WHERE namespace_id = ? AND confidence != 'deprecated' "
        "ORDER BY last_accessed DESC LIMIT ?",
        (namespace_id, limit),
    ).fetchall()
    return [Memory(**dict(r)) for r in rows]


def _cross_namespace_patterns(
    conn: sqlite3.Connection, exclude_ns: str, limit: int = 3
) -> list[Memory]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM memories "
        "WHERE type IN ('pattern', 'preference') AND confidence = 'verified' "
        "AND namespace_id != ? "
        "ORDER BY access_count DESC LIMIT ?",
        (exclude_ns, limit),
    ).fetchall()
    return [Memory(**dict(r)) for r in rows]


def build_context(
    conn: sqlite3.Connection,
    *,
    namespace_id: str,
    task_hint: str | None = None,
    limit: int = 10,
    weights: dict[str, float],
    decay_lambda: float = 0.01,
    embedder: EmbeddingProvider | None = None,
) -> list[Memory]:
    """Assemble the auto-context set via guaranteed per-bucket quotas.

    Each bucket is ranked by its native signal, then a reserved share of the
    ``limit`` slots is taken from each (recency first) so the "where we left
    off" signal can't be evicted by the relevance/access-dominated composite.
    Remaining slots are backfilled from the combined pool by composite score;
    the final list is presented in composite order.
    """
    # Bucket 1: task-relevant, already composite-scored and ordered by search().
    task_bucket: list[Memory] = []
    if task_hint and task_hint.strip():
        task_n = max(1, math.ceil(limit * _TASK_RATIO))
        task_bucket = search.search(
            conn,
            query=task_hint,
            namespace_id=namespace_id,
            limit=task_n,
            weights=weights,
            decay_lambda=decay_lambda,
            embedder=embedder,
        )

    # Bucket 2: recently active in this namespace, ordered by last_accessed DESC.
    recent_bucket = _recently_active(conn, namespace_id, limit)
    for mem in recent_bucket:
        mem.score = _score(mem, weights, decay_lambda, relevance=0.5)

    # Bucket 3: cross-namespace verified patterns/preferences, by access_count.
    cross_bucket = _cross_namespace_patterns(conn, exclude_ns=namespace_id)
    for mem in cross_bucket:
        mem.score = _score(mem, weights, decay_lambda, relevance=0.5)

    # De-duplicate across buckets, keeping each memory's highest score (a task
    # hit that also shows up in the recency bucket keeps its richer relevance).
    best: dict[str, Memory] = {}
    for mem in (*task_bucket, *recent_bucket, *cross_bucket):
        current = best.get(mem.id)
        if current is None or (mem.score or 0.0) > (current.score or 0.0):
            best[mem.id] = mem

    # Apply the architecture/decision boost exactly once, after de-dup. The
    # boost is uniform, so it never changes which instance won the max above.
    for mem in best.values():
        if mem.type.value in _BOOST_TYPES and mem.score is not None:
            mem.score += _BOOST_AMOUNT

    # Guaranteed-quota selection. Fill recency first — it's the intent the old
    # score-and-collapse design starved — then task relevance, then cross-ns.
    selected: list[str] = []
    chosen: set[str] = set()

    def take(bucket: list[Memory], quota: int) -> None:
        taken = 0
        for mem in bucket:
            if len(chosen) >= limit or taken >= quota:
                return
            if mem.id not in chosen:
                chosen.add(mem.id)
                selected.append(mem.id)
                taken += 1

    recent_quota = max(1, math.ceil(limit * _RECENT_RATIO))
    task_quota = max(1, math.ceil(limit * _TASK_RATIO)) if task_bucket else 0

    take(recent_bucket, recent_quota)
    take(task_bucket, task_quota)
    take(cross_bucket, _CROSS_NS_QUOTA)

    # Backfill any unused slots from the combined pool by composite score.
    if len(chosen) < limit:
        leftovers = sorted(
            (m for mid, m in best.items() if mid not in chosen),
            key=lambda m: m.score or 0.0,
            reverse=True,
        )
        for mem in leftovers:
            if len(chosen) >= limit:
                break
            chosen.add(mem.id)
            selected.append(mem.id)

    # Present the surfaced set in composite order.
    ranked = sorted((best[mid] for mid in selected), key=lambda m: m.score or 0.0, reverse=True)
    return ranked[:limit]
