"""The ``query_acts`` table: what a session did with what a query returned.

The paraphrase question set labels a real query with the memory the session
then acted on. Measured 2026-11-02, that label could not be recovered from the
logs afterwards: ``access_log`` rows carry a long-lived MCP session id, updates
and relations carry no session at all, so another agent's act read as this
one's. So each act is tied to its query when it happens: the newest query of
the same session whose ranked hits (``rank``) or extras (``rank`` NULL)
included the memory. An act no query of the session surfaced is still a row,
with no query - the memory came from somewhere search did not show.

Bookkeeping, never part of the answer: ``record`` swallows database errors and
never commits, like ``query_log``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from .models import utcnow_iso

logger = logging.getLogger(__name__)

ACTS = ("fetch", "excerpt", "update", "relate", "forget")
# How far back a session's queries are searched for the one that surfaced a memory.
LOOKBACK_QUERIES = 200


def _ids(raw: object) -> list[str]:
    try:
        ids = json.loads(raw) if isinstance(raw, str) else []
    except ValueError:
        return []
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


def _surfaced(conn: sqlite3.Connection, session_id: str) -> list[tuple[str, list, list]]:
    """The session's newest queries, newest first: (id, ranked hits, extras)."""
    rows = conn.execute(
        "SELECT id, result_ids, extra_ids FROM query_log WHERE session_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (session_id, LOOKBACK_QUERIES),
    ).fetchall()
    return [(qid, _ids(hits), _ids(extras)) for qid, hits, extras in rows]


def _attribute(queries: list, memory_id: str) -> tuple[str | None, int | None]:
    for qid, hits, extras in queries:
        if memory_id in hits:
            return qid, hits.index(memory_id) + 1
        if memory_id in extras:
            return qid, None
    return None, None


def record(
    conn: sqlite3.Connection, *, act: str, memory_ids: list[str], session_id: str | None
) -> int:
    """Log one act on each distinct memory. Returns the rows written.

    Without a session there is no query to tie it to; the act is still kept.
    """
    if act not in ACTS:
        raise ValueError(f"unknown act {act!r}; expected one of {ACTS}")
    ids = list(dict.fromkeys(m for m in memory_ids if m))
    if not ids:
        return 0
    try:
        queries = _surfaced(conn, session_id) if session_id else []
        now = utcnow_iso()
        rows = [
            (str(uuid.uuid4()), session_id, act, mid, *_attribute(queries, mid), now) for mid in ids
        ]
        conn.executemany(
            "INSERT INTO query_acts(id, session_id, act, memory_id, query_id, rank, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    except sqlite3.Error as exc:
        logger.warning("query_acts write skipped: %s", exc)
        return 0
    return len(rows)
