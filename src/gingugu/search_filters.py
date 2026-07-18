"""Filtered search and metadata-only listing (``memory_search`` backend).

With a query string, delegates to the hybrid engine in ``search.py``;
without one, lists by metadata filters ordered by ``sort_by``. Split out
of ``search.py`` to keep each module within the repo's size discipline.
"""

from __future__ import annotations

import sqlite3

from . import decay
from .embeddings import EmbeddingProvider
from .models import Confidence, Memory
from .search import _CANDIDATE_MULTIPLIER, search
from .search_common import BASE_COLUMNS, build_filters


def advanced_search(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    namespace_id: str | list[str] | None = None,
    type: str | None = None,
    min_confidence: Confidence | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    sort_by: str = "relevance",
    include_deprecated: bool = False,
    limit: int = 10,
    weights: dict[str, float] | None = None,
    decay_lambda: float = 0.01,
    tags: list[str] | None = None,
    embedder: EmbeddingProvider | None = None,
) -> list[Memory]:
    """Filtered search. With a query, delegates to FTS5 + composite ranking;
    without one, lists by metadata filters ordered by ``sort_by``."""
    if query and query.strip():
        results = search(
            conn,
            query=query,
            namespace_id=namespace_id,
            type=type,
            min_confidence=min_confidence,
            include_deprecated=include_deprecated,
            created_after=created_after,
            created_before=created_before,
            limit=max(limit * _CANDIDATE_MULTIPLIER, limit),
            weights=weights if sort_by in ("relevance", "decay_score") else None,
            decay_lambda=decay_lambda,
            tags=tags,
            embedder=embedder,
        )
    else:
        results = _list_by_filters(
            conn,
            namespace_id=namespace_id,
            type=type,
            min_confidence=min_confidence,
            created_after=created_after,
            created_before=created_before,
            include_deprecated=include_deprecated,
            limit=max(limit * _CANDIDATE_MULTIPLIER, limit),
            weights=weights,
            decay_lambda=decay_lambda,
            tags=tags,
        )

    if sort_by in ("relevance", "decay_score"):
        results.sort(key=lambda m: m.score or 0.0, reverse=True)
    elif sort_by == "created":
        results.sort(key=lambda m: m.created_at, reverse=True)
    elif sort_by == "accessed":
        results.sort(key=lambda m: m.last_accessed, reverse=True)
    return results[:limit]


def _list_by_filters(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | list[str] | None,
    type: str | None,
    min_confidence: Confidence | None,
    created_after: str | None,
    created_before: str | None,
    include_deprecated: bool,
    limit: int,
    weights: dict[str, float] | None,
    decay_lambda: float = 0.01,
    tags: list[str] | None = None,
) -> list[Memory]:
    where, params = build_filters(
        alias="memories",
        namespace_id=namespace_id,
        type=type,
        min_confidence=min_confidence,
        include_deprecated=include_deprecated,
        created_after=created_after,
        created_before=created_before,
        tags=tags,
    )
    clause = f"WHERE {' AND '.join(where)} " if where else ""
    sql = f"SELECT {BASE_COLUMNS} FROM memories {clause}ORDER BY last_accessed DESC LIMIT ?"
    rows = conn.execute(sql, [*params, limit]).fetchall()

    out: list[Memory] = []
    for row in rows:
        mem = Memory(**dict(row))
        # No query → relevance defaults to 0.5 so freshness/confidence drive order.
        mem.score = (
            decay.score_memory(
                relevance=0.5,
                last_confirmed=mem.last_confirmed,
                updated_at=mem.updated_at,
                created_at=mem.created_at,
                access_count=mem.access_count,
                confidence=mem.confidence.value,
                weights=weights,
                decay_lambda=decay_lambda,
            )
            if weights
            else 0.5
        )
        out.append(mem)
    return out
