"""Single-instance lock for the dream pass.

A scheduled run and a hand-run must not overlap. They would not corrupt
anything - the queue's identity index makes a duplicate insert an update - but
they would burn two cores computing the same PageRank and log two contradictory
reports of the same graph.

The lock is a database row rather than a lockfile for two reasons. It behaves
identically on macOS, Linux and Windows, where advisory file locking does not.
And it is recoverable without anyone reasoning about orphaned file handles: the
row carries an expiry, so a holder that was killed mid-pass blocks the next run
for one window and no longer.

Acquisition is done inside ``BEGIN IMMEDIATE``. SQLite grants the write lock to
exactly one connection, so two processes racing for the row cannot both read
"free" and both write "mine".
"""

from __future__ import annotations

import logging
import os
import socket
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from .models import utcnow_iso

logger = logging.getLogger(__name__)

# How long a holder's claim stands before another process may take it. Sized
# well above a normal pass (~25s on a 1,900-memory brain) so a slow run is never
# stolen from, and well below a scheduling interval so a crash costs one tick.
DEFAULT_LEASE_SECONDS = 900


def _holder_id() -> str:
    """A token identifying this *acquisition*, not this process.

    The host and pid are here so a stuck lock names something a person can go
    look at. The uuid is here because the pid alone is not unique enough: the
    MCP server is long-lived, so a hand-run that overran its lease and a later
    run from the same process would carry identical tokens, and the ``holder =
    ?`` guard in ``release`` would wave through exactly the theft it exists to
    prevent. A lease identifier has to be per-lease.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _parse(stamp: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def acquire(conn: sqlite3.Connection, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> str | None:
    """Take the lock, or return ``None`` if someone else holds a live one.

    Returns the holder token on success; pass it back to ``release``. A lock
    whose ``expires_at`` has passed is taken over, and an unparseable one is
    treated as expired - a lock nobody can read the expiry of is a lock nobody
    can ever release, which is worse than a stolen one.
    """
    now = datetime.now(UTC)
    token = _holder_id()
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error:
        logger.debug("could not begin lock transaction", exc_info=True)
        return None
    try:
        row = conn.execute("SELECT holder, expires_at FROM dream_lock WHERE id = 1").fetchone()
        if row is not None:
            expires = _parse(row[1])
            if expires is not None and expires > now:
                conn.rollback()
                logger.info("dream lock held by %s until %s", row[0], row[1])
                return None
        conn.execute(
            "INSERT INTO dream_lock (id, holder, acquired_at, expires_at) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET holder = excluded.holder, "
            "acquired_at = excluded.acquired_at, expires_at = excluded.expires_at",
            (
                token,
                utcnow_iso(),
                (now + timedelta(seconds=lease_seconds)).isoformat(),
            ),
        )
        conn.commit()
        return token
    except sqlite3.Error:
        conn.rollback()
        logger.warning("dream lock acquire failed", exc_info=True)
        return None


def release(conn: sqlite3.Connection, token: str) -> None:
    """Give up the lock, but only if we still hold it. Never raises.

    The ``holder = ?`` guard matters when a pass overran its lease: by then
    someone else legitimately owns the row, and deleting it would hand a third
    process a lock two others think they hold.
    """
    try:
        conn.execute("DELETE FROM dream_lock WHERE id = 1 AND holder = ?", (token,))
        conn.commit()
    except sqlite3.Error:
        logger.warning("dream lock release failed for %s", token, exc_info=True)


@contextmanager
def held(
    conn: sqlite3.Connection, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> Iterator[str | None]:
    """Hold the lock for the block, yielding the token or ``None`` if busy.

    The body must check for ``None``. Yielding it rather than raising keeps
    "someone else is already dreaming" an ordinary outcome a scheduled run
    reports and exits on, not an exception a cron job emails about nightly.
    """
    token = acquire(conn, lease_seconds=lease_seconds)
    try:
        yield token
    finally:
        if token is not None:
            release(conn, token)
