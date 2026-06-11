"""Auto-context generation for session start (``memory_context``).

The result set is a *union* of three buckets, de-duplicated and sorted by
composite score (see docs/architecture.md → memory_context):

1. Task-relevant (if ``task_hint``) — FTS5 search scoped to namespace.
2. Recently active in this namespace — by ``last_accessed``, excluding deprecated.
3. Cross-namespace high-confidence patterns — pattern/preference + verified.

Types ``architecture`` and ``decision`` get a +0.1 score boost (disproportionately
useful at session start).
"""

from __future__ import annotations

import math
import sqlite3

from . import decay, search
from .models import Memory

_BOOST_TYPES = {"architecture", "decision"}
_BOOST_AMOUNT = 0.1

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
    decay_lambda: float = 0.05,
) -> list[Memory]:
    """Assemble and rank the auto-context memory set."""
    by_id: dict[str, Memory] = {}

    # Bucket 1: task-relevant (already composite-scored by search()).
    if task_hint and task_hint.strip():
        task_n = max(1, math.ceil(limit * 0.5))
        for mem in search.search(
            conn,
            query=task_hint,
            namespace_id=namespace_id,
            limit=task_n,
            weights=weights,
            decay_lambda=decay_lambda,
        ):
            by_id[mem.id] = mem

    # Bucket 2: recently active in this namespace.
    for mem in _recently_active(conn, namespace_id, limit):
        if mem.id not in by_id:
            mem.score = _score(mem, weights, decay_lambda, relevance=0.5)
            by_id[mem.id] = mem

    # Bucket 3: cross-namespace verified patterns/preferences.
    for mem in _cross_namespace_patterns(conn, exclude_ns=namespace_id):
        if mem.id not in by_id:
            mem.score = _score(mem, weights, decay_lambda, relevance=0.5)
            by_id[mem.id] = mem

    # All buckets are scored boost-free above; apply the type boost exactly
    # once here so architecture/decision memories rank up uniformly.
    for mem in by_id.values():
        if mem.type.value in _BOOST_TYPES and mem.score is not None:
            mem.score += _BOOST_AMOUNT

    ranked = sorted(by_id.values(), key=lambda m: m.score or 0.0, reverse=True)
    return ranked[:limit]
