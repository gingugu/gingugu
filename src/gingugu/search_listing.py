"""The retrieval strategies behind ``memory_search``'s ``sort_by``.

One strategy per way of ordering the corpus: by a column (SQL does the
ordering and the limit), by the composite decay score (Python does the
ordering, so the whole matching corpus is scored), by the FTS match set
ordered on a column, and by exact id.

They share one rule. **Rows are selected in the order they are returned.**
Sorting a pool that was truncated by a *different* ordering does not
reorder the corpus, it reorders a biased sample of it: anything that lost
the earlier cut is unreachable no matter how well it matches the sort. So
none of these functions re-sorts what it fetched, and none of them
oversamples. ``search_filters.advanced_search`` builds the WHERE fragments
and picks between them.
"""

from __future__ import annotations

import sqlite3

from . import decay
from .models import Memory
from .search import build_match_query
from .search_common import BASE_COLUMNS, COLUMNS

# Column list for the Python-side scoring pass. Deliberately excludes
# `content`: scoring one namespace means scoring every row in it, and the
# bodies are the expensive part. Full rows are fetched for the winners only.
_SCORING_COLUMNS = "id, last_confirmed, updated_at, created_at, access_count, confidence"


def _where_clause(where: list[str]) -> str:
    return f"WHERE {' AND '.join(where)} " if where else ""


def fetch_by_ids(conn: sqlite3.Connection, ids: list[str]) -> tuple[list[Memory], list[str]]:
    """Fetch memories by exact ID, preserving the requested order.

    An ID fetch is an explicit read - the caller named the memory - so
    deprecated memories are returned too (a reconciliation sweep must be able
    to inspect what it is reconciling). Returns ``(found, missing_ids)``.
    """
    if not ids:
        return [], []
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT {BASE_COLUMNS} FROM memories WHERE id IN ({placeholders})", ids
    ).fetchall()
    by_id = {row["id"]: Memory(**dict(row)) for row in rows}
    found = [by_id[mid] for mid in ids if mid in by_id]
    missing = [mid for mid in ids if mid not in by_id]
    return found, missing


def no_query_score(
    row: sqlite3.Row,
    weights: dict[str, float] | None,
    decay_lambda: float,
) -> float:
    """Composite score for a listing row.

    With no query there is nothing to be relevant *to*, so relevance is a
    flat 0.5 and freshness, access and confidence are what actually order
    the result. Without ``weights`` there is no composite to compute and
    every row scores that same 0.5.
    """
    if not weights:
        return 0.5
    return decay.score_memory(
        relevance=0.5,
        last_confirmed=row["last_confirmed"],
        updated_at=row["updated_at"],
        created_at=row["created_at"],
        access_count=row["access_count"],
        confidence=row["confidence"],
        weights=weights,
        decay_lambda=decay_lambda,
    )


def list_by_column(
    conn: sqlite3.Connection,
    *,
    where: list[str],
    params: list[object],
    order_column: str,
    limit: int,
    weights: dict[str, float] | None = None,
    decay_lambda: float = 0.01,
) -> list[Memory]:
    """List by metadata filters, ordered in SQL by ``order_column``.

    ``id`` breaks ties so equal timestamps - routine, since memories written
    in one operation share a second - resolve the same way on every call
    rather than by storage order.
    """
    sql = (
        f"SELECT {BASE_COLUMNS} FROM memories {_where_clause(where)}"
        f"ORDER BY {order_column} DESC, id LIMIT ?"
    )
    rows = conn.execute(sql, [*params, limit]).fetchall()

    out: list[Memory] = []
    for row in rows:
        mem = Memory(**dict(row))
        mem.score = no_query_score(row, weights, decay_lambda)
        out.append(mem)
    return out


def list_by_score(
    conn: sqlite3.Connection,
    *,
    where: list[str],
    params: list[object],
    limit: int,
    weights: dict[str, float] | None = None,
    decay_lambda: float = 0.01,
) -> list[Memory]:
    """List by the composite decay score, with no query to rank against.

    The score is computed in Python, so SQLite cannot order by it and the
    whole matching corpus has to be scored to know which rows win. That is
    affordable because the scoring pass reads no ``content``: it costs six
    small columns per matching memory, and only the winners are then fetched
    in full. Scoring a truncated pool instead would be exactly the defect
    this module exists to avoid.
    """
    if not weights:
        # Every score would be an identical 0.5, so there is nothing to rank.
        # Fall back to the listing order rather than returning an arbitrary
        # slice of a flat tie.
        return list_by_column(
            conn,
            where=where,
            params=params,
            order_column="last_accessed",
            limit=limit,
            weights=weights,
            decay_lambda=decay_lambda,
        )

    rows = conn.execute(
        f"SELECT {_SCORING_COLUMNS} FROM memories {_where_clause(where)}", params
    ).fetchall()
    ranked = sorted(
        ((no_query_score(row, weights, decay_lambda), row["id"]) for row in rows),
        key=lambda pair: (-pair[0], pair[1]),
    )[:limit]
    score_by_id = {mid: score for score, mid in ranked}
    found, _ = fetch_by_ids(conn, [mid for _, mid in ranked])
    for mem in found:
        mem.score = score_by_id[mem.id]
    return found


def match_ordered_by(
    conn: sqlite3.Connection,
    *,
    query: str,
    where: list[str],
    params: list[object],
    order_column: str,
    limit: int,
) -> list[Memory]:
    """The FTS match set ordered by a column, limited in SQL.

    No BM25 ranking and no semantic cohort: the caller asked for the newest
    (or least recently read) memories mentioning the query, and relevance has
    no vote in that. Results carry no ``score``, because there is no ranking
    behind them to report. ``where``/``params`` must be built against the
    ``m`` alias this join uses.
    """
    match = build_match_query(query)
    if match is None:
        return []
    sql = (
        f"SELECT {COLUMNS} FROM memories_fts "
        "JOIN memories m ON m.rowid = memories_fts.rowid "
        f"WHERE {' AND '.join(['memories_fts MATCH ?', *where])} "
        f"ORDER BY m.{order_column} DESC, m.id LIMIT ?"
    )
    rows = conn.execute(sql, [match, *params, limit]).fetchall()
    return [Memory(**dict(row)) for row in rows]
