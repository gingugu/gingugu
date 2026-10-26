"""Migration 017: ``board_posts`` and ``board_reads``, the agent board.

Agents sharing a brain had no way to say anything to each other except by
writing a memory and hoping the other one recalled it. A post is not a memory:
it is addressed, it is read once, and a task on it is claimed and finished.
Storing posts as memories would put chatter into the graph and the ranking, so
they get tables of their own and a post is promoted to a memory only on
purpose.

``namespace_id`` cascades like ``memories`` does, so a board lives and dies
with its namespace. ``parent_id`` makes a reply; deleting a post takes its
thread with it. ``recipient`` is a persona name or ``*`` for everyone (``to``
is an SQLite keyword). ``kind`` and ``status`` are plain TEXT validated in
Python, not ``CHECK`` constraints, because SQLite cannot alter a constraint in
place and a new kind should not need a table rebuild.

``board_reads`` is one row per reader per post: unread is the absence of a
row, so marking read is an idempotent insert and the inbox is an anti-join.

Never pruned. A finished task drops out of the inbox by status, not by delete.
"""

from __future__ import annotations

import sqlite3

_SCHEMA_V17 = """
CREATE TABLE board_posts (
    id              TEXT PRIMARY KEY,
    namespace_id    TEXT NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
    parent_id       TEXT REFERENCES board_posts(id) ON DELETE CASCADE,
    author          TEXT NOT NULL,
    author_session  TEXT,
    recipient       TEXT NOT NULL,
    kind            TEXT NOT NULL,
    status          TEXT NOT NULL,
    claimed_by      TEXT,
    body            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX idx_board_posts_inbox ON board_posts(namespace_id, recipient, status);
CREATE INDEX idx_board_posts_parent ON board_posts(parent_id);

CREATE TABLE board_reads (
    post_id  TEXT NOT NULL REFERENCES board_posts(id) ON DELETE CASCADE,
    reader   TEXT NOT NULL,
    read_at  TEXT NOT NULL,
    PRIMARY KEY (post_id, reader)
);
"""


def _migration_017_board(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V17)
