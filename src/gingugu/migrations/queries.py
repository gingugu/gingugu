"""Migration 015: ``query_log``, the questions asked of the brain.

``access_log`` says which memories a session read and never what it asked for.
It keeps one row per *returned* memory, so a query that found nothing leaves no
trace there at all - and those misses are exactly what a paraphrase question
set needs to measure. So the question gets its own table, one row per query,
carrying the results in rank order as they came back.

``result_ids`` is a JSON array rather than a join table. It is written once,
read whole, and its order is the point; a join table would need a rank column
to keep what the array keeps for free.

``namespaces`` is the scope the query ran in: a comma-separated list, or ``*``
for every namespace (an unscoped read, or a scoped one that widened).

Never pruned. ``access_log`` ages out because ``access_count`` already carries
its aggregate; nothing summarises this table, so the rows are the only copy.
"""

from __future__ import annotations

import sqlite3

_SCHEMA_V15 = """
CREATE TABLE query_log (
    id          TEXT PRIMARY KEY,
    session_id  TEXT,
    tool        TEXT NOT NULL,
    query       TEXT NOT NULL,
    namespaces  TEXT NOT NULL,
    result_ids  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_query_log_created ON query_log(created_at);
"""


def _migration_015_query_log(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V15)
