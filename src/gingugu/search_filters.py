"""Filtered search and metadata-only listing (``memory_search`` backend).

Picks the retrieval strategy that answers the call: the hybrid engine in
``search.py`` when relevance is the sort, one of the ordered listings in
``search_listing.py`` otherwise. Split out of ``search.py`` to keep each
module within the repo's size discipline.

``sort_by`` chooses the strategy rather than being applied on top of one.
That is the point: a sort layered over an already-truncated pool reorders
a biased sample instead of the corpus, so whatever lost the earlier cut
can never appear however well it matches the sort.
"""

from __future__ import annotations

import sqlite3

from .embeddings import EmbeddingProvider
from .models import Confidence, Memory
from .search import search
from .search_common import build_filters
from .search_listing import fetch_by_ids, list_by_column, list_by_score, match_ordered_by

# Sorts whose key is a column, so SQLite can order the corpus itself.
# Anything else is scored in Python (``relevance``, ``decay_score``).
_ORDER_COLUMNS = {"created": "created_at", "accessed": "last_accessed"}

__all__ = ["advanced_search", "fetch_by_ids"]


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
    claims: str | None = None,
    orphans: bool = False,
    embedder: EmbeddingProvider | None = None,
) -> list[Memory]:
    """Filtered search. With a query, delegates to FTS5 + composite ranking;
    without one, lists by metadata filters ordered by ``sort_by``.

    ``claims`` restricts to memories carrying open (or contradicted) state
    claims — the reconciliation backlog as a first-class corpus, usable with
    or without a query. ``orphans`` does the same for the graph backlog:
    memories no relation touches, which spreading activation can never reach.

    A column ``sort_by`` (``created``/``accessed``) orders the *whole*
    matching corpus before the limit, so the answer is the true
    newest/least-recently-read and does not change with ``limit``. With a
    query that corpus is the FTS match set: a date sort asks a question
    relevance cannot answer, so the semantic cohort - whose membership is
    itself a relevance judgement - does not vote in it.
    """
    filters = dict(
        namespace_id=namespace_id,
        type=type,
        min_confidence=min_confidence,
        include_deprecated=include_deprecated,
        created_after=created_after,
        created_before=created_before,
        tags=tags,
        claims=claims,
        orphans=orphans,
    )
    order_column = _ORDER_COLUMNS.get(sort_by)

    if query and query.strip():
        if order_column is None:
            return search(
                conn,
                query=query,
                limit=limit,
                weights=weights,
                decay_lambda=decay_lambda,
                embedder=embedder,
                **filters,  # type: ignore[arg-type]
            )
        where, params = build_filters(**filters)  # type: ignore[arg-type]
        return match_ordered_by(
            conn,
            query=query,
            where=where,
            params=params,
            order_column=order_column,
            limit=limit,
        )

    where, params = build_filters(alias="memories", **filters)  # type: ignore[arg-type]
    if order_column is None:
        return list_by_score(
            conn,
            where=where,
            params=params,
            limit=limit,
            weights=weights,
            decay_lambda=decay_lambda,
        )
    return list_by_column(
        conn,
        where=where,
        params=params,
        order_column=order_column,
        limit=limit,
        weights=weights,
        decay_lambda=decay_lambda,
    )
