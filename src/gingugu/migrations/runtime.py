"""Migrations for runtime coordination tables - state *about* the store.

Everything in ``schema`` describes memories. Everything here describes the
processes that touch them: when the store was last used, and who is currently
running a background pass over it. The split is deliberate. These tables carry
no knowledge, survive no export, and a store that lost them would lose nothing
a person put there.
"""

from __future__ import annotations

import sqlite3

from ..models import utcnow_iso

# --- Migration 012: activity heartbeat + dream lock --------------------------
#
# Two single-row tables, both existing so a scheduled pass can answer a question
# it otherwise cannot: "should I be running right now?"
#
# ``activity`` is the heartbeat. The MCP server stamps it on every tool call, so
# "last active" means *someone used the brain*, not "a process is alive" - an
# idle editor holds the server open all day without touching a memory, and that
# is precisely when the pass should run.
#
# The alternative was deriving activity from existing timestamps, and it does
# not survive contact with the schema: reads live in ``access_log``, writes in
# ``memories.created_at``/``updated_at``, and edges in ``relations``. A MAX over
# three tables is both a wrong answer waiting to happen and an unindexed scan -
# ``idx_access_log_memory_time`` leads with ``memory_id``, so a bare
# ``MAX(accessed_at)`` cannot use it.
#
# ``dream_lock`` stops two passes running at once. It is a row rather than a
# lockfile because a row is the same on every OS we ship to, and because a
# crashed holder is recoverable without anyone reasoning about file handles:
# the lock carries an expiry, and an expired lock is takeable.

_SCHEMA_V12 = """
CREATE TABLE activity (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    last_active_at TEXT NOT NULL,
    source         TEXT
);

CREATE TABLE dream_lock (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    holder      TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
"""


def _migration_012_activity_and_lock(conn: sqlite3.Connection) -> None:
    """Add the activity heartbeat and the dream-pass single-instance lock.

    Both tables are pinned to a single row by ``CHECK (id = 1)``. That is not
    tidiness: it makes the heartbeat an ``UPDATE`` on a known row rather than an
    upsert, which is the cheapest write SQLite has, and it means the lock cannot
    be held twice however badly a caller misuses it.

    ``activity`` is seeded at migration time rather than left empty. An empty
    heartbeat table and a genuinely idle store are indistinguishable to a
    reader, and the safer reading of "I have never seen activity" is *not* to
    assume the store is idle and start work on it. Seeding makes the first
    scheduled run wait one full idle window after upgrade, which is correct: we
    have no evidence about what happened before this moment.

    ``dream_lock`` is deliberately left EMPTY - no row means no holder, which is
    the honest starting state and needs no special case in the acquire path.
    """
    conn.executescript(_SCHEMA_V12)
    conn.execute(
        "INSERT INTO activity (id, last_active_at, source) VALUES (1, ?, 'migration')",
        (utcnow_iso(),),
    )
