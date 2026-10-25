"""``/events``: the agent's recall activity as a time-ordered feed.

``access_log`` and ``query_log`` rows are merged into one stream. Timestamps
are stored as uniform ``+00:00`` microsecond ISO strings, so string comparison
in SQL is time comparison. With no cursor the feed returns nothing and hands
back "now" (the newest stored timestamp) so a live viewer starts at the edge.

A page never ends partway through one timestamp. ``access.record`` stamps every
memory a recall returned with the same instant, and the next page asks for
events strictly ``after`` the cursor, so a split batch would strand its tail.
When the limit lands inside a batch, the page stops before it - or, if the
batch is the whole page, returns the batch entire, over the limit.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from .db import parse_limit
from .env import ApiError

DEFAULT_LIMIT = 500
MAX_LIMIT = 2000


def _stamp(params: dict[str, str], key: str) -> str | None:
    raw = params.get(key)
    if raw is None:
        return None
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        raise ApiError(400, f"{key} must be an ISO timestamp") from None
    return raw


def _latest(conn: sqlite3.Connection) -> str | None:
    newest = [
        conn.execute("SELECT MAX(accessed_at) FROM access_log").fetchone()[0],
        conn.execute("SELECT MAX(created_at) FROM query_log").fetchone()[0],
    ]
    return max((t for t in newest if t), default=None)


def _fetch(conn: sqlite3.Connection, bounds: str, args: list[str], limit: int) -> list[dict]:
    """Both logs filtered by ``bounds`` (``{t}`` = the table's time column)."""
    found = [
        {
            "kind": "access",
            "at": r["accessed_at"],
            "memory_id": r["memory_id"],
            "context": r["context"],
        }
        for r in conn.execute(
            "SELECT memory_id, accessed_at, context FROM access_log"
            f" WHERE {bounds.format(t='accessed_at')} ORDER BY accessed_at, id LIMIT ?",
            [*args, limit],
        )
    ]
    found += [
        {
            "kind": "query",
            "at": r["created_at"],
            "tool": r["tool"],
            "query": r["query"],
            "namespaces": json.loads(r["namespaces"]),
            "result_ids": json.loads(r["result_ids"]),
            "session_id": r["session_id"],
        }
        for r in conn.execute(
            "SELECT tool, query, namespaces, result_ids, session_id, created_at FROM query_log"
            f" WHERE {bounds.format(t='created_at')} ORDER BY created_at, id LIMIT ?",
            [*args, limit],
        )
    ]
    found.sort(key=lambda e: e["at"])
    return found


def _page(conn: sqlite3.Connection, bounds: str, args: list[str], limit: int) -> list[dict]:
    """Up to ``limit`` events, cut only between two different timestamps."""
    found = _fetch(conn, bounds, args, limit + 1)
    if len(found) <= limit:
        return found
    edge = found[limit - 1]["at"]
    if found[limit]["at"] != edge:
        return found[:limit]
    kept = [e for e in found[:limit] if e["at"] != edge]
    if kept:
        return kept
    # One batch outgrows the whole page: hand it back entire, cursor past it.
    return _fetch(conn, "{t} = ?", [edge], MAX_LIMIT * 10)


def events(conn: sqlite3.Connection, params: dict[str, str]) -> dict:
    after = _stamp(params, "after")
    start, end = _stamp(params, "from"), _stamp(params, "to")
    if start is not None and end is None:
        raise ApiError(400, "from needs to")
    if start is not None and after is not None:
        raise ApiError(400, "from and after are exclusive")
    limit = parse_limit(params, DEFAULT_LIMIT, MAX_LIMIT)
    if start is None and after is None:
        if end is not None:
            raise ApiError(400, "to needs from or after")
        return {"ok": True, "events": [], "cursor": _latest(conn)}
    lower, low = ("{t} >= ?", start) if start is not None else ("{t} > ?", after)
    bounds, args = lower, [low]
    if end is not None:
        bounds, args = f"{lower} AND {{t}} < ?", [low, end]
    found = _page(conn, bounds, args, limit)
    cursor = found[-1]["at"] if found else (end if start is not None else after)
    return {"ok": True, "events": found, "cursor": cursor}
