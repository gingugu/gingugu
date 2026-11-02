"""The ``query_log`` table: what was asked of the brain, and what came back.

One row per query from ``memory_recall``, a query-bearing ``memory_search``, a
``memory_context`` task hint, and the involuntary-recall hook. It is the raw
material for a paraphrase question set: real queries in the asker's own words,
paired later with the memory the session went on to act on.

A zero-hit query is still a row. Those are the misses, and the misses are the
point - ``access_log`` could never record them because it only has a row for a
memory that was returned.

Logging is bookkeeping, never part of the answer. ``record`` swallows database
errors so a retrieval can never fail because its question could not be
written down, and like ``access`` it never commits: callers own the
transaction boundary.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from .models import utcnow_iso

logger = logging.getLogger(__name__)

# A prompt can carry a pasted file. The question is in the first few lines;
# the rest would only grow the table.
MAX_QUERY_CHARS = 2000

ALL_NAMESPACES = "*"


def record(
    conn: sqlite3.Connection,
    *,
    tool: str,
    query: str | None,
    result_ids: list[str],
    namespaces: list[str] | None,
    session_id: str | None = None,
    extra_ids: list[str] | None = None,
) -> bool:
    """Log one query. Returns whether a row was written.

    ``namespaces`` is the scope the query actually ran in; ``None`` means every
    namespace. ``result_ids`` keeps its rank order, de-duplicated. ``extra_ids``
    are what came back beside the ranked hits (related extras, capability
    pointers), so an act on one of them still finds this query; they never
    repeat a ranked hit. A blank query is not a question and is not logged.
    """
    text = (query or "").strip()[:MAX_QUERY_CHARS]
    if not text:
        return False
    scope = ",".join(namespaces) if namespaces else ALL_NAMESPACES
    ranked = list(dict.fromkeys(mid for mid in result_ids if mid))
    extras = [m for m in dict.fromkeys(m for m in extra_ids or () if m) if m not in ranked]
    row = [str(uuid.uuid4()), session_id, tool, text, scope, json.dumps(ranked), utcnow_iso()]
    cols = "id, session_id, tool, query, namespaces, result_ids, created_at"
    if extras:  # only then: a store before migration 022 still logs everything else
        cols, row = cols + ", extra_ids", row + [json.dumps(extras)]
    try:
        conn.execute(f"INSERT INTO query_log({cols}) VALUES ({', '.join('?' * len(row))})", row)
    except sqlite3.Error as exc:
        logger.warning("query_log write skipped: %s", exc)
        return False
    return True
