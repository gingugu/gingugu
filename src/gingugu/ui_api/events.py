"""``/events``: the agent's recall activity as a time-ordered feed.

``access_log`` and ``query_log`` rows are merged into one stream. Timestamps
are stored as uniform ``+00:00`` microsecond ISO strings, so string comparison
in SQL is time comparison. With no cursor the feed returns nothing and hands
back "now" (the newest stored timestamp) so a live viewer starts at the edge.
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
    return found[:limit]


def events(conn: sqlite3.Connection, params: dict[str, str]) -> dict:
    after = _stamp(params, "after")
    start, end = _stamp(params, "from"), _stamp(params, "to")
    if (start is None) != (end is None):
        raise ApiError(400, "from and to must be given together")
    limit = parse_limit(params, DEFAULT_LIMIT, MAX_LIMIT)
    if start is not None and end is not None:
        found = _fetch(conn, "{t} >= ? AND {t} < ?", [start, end], limit)
        cursor = found[-1]["at"] if found else end
    elif after is not None:
        found = _fetch(conn, "{t} > ?", [after], limit)
        cursor = found[-1]["at"] if found else after
    else:
        found, cursor = [], _latest(conn)
    return {"ok": True, "events": found, "cursor": cursor}
