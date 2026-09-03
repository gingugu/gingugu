"""The activity heartbeat: when was this brain last actually used.

One row, stamped by the MCP server on every tool call, read by the scheduled
dream pass to decide whether to run at all.

The distinction that makes this table worth having: **a running server is not
an active user.** An editor holds the MCP server open for eight hours whether
or not anyone stores a memory in it, so process liveness answers the wrong
question. What the pass needs to know is when someone last *reached for* the
brain, and only a tool call knows that.

Failures here are swallowed. A heartbeat that cannot be written must never take
a tool call down with it: the worst case is a pass that declines to run, and
that is strictly better than a memory that failed to store.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from .models import utcnow_iso

logger = logging.getLogger(__name__)


def stamp(conn: sqlite3.Connection, source: str) -> None:
    """Record that the brain was used just now. Never raises.

    ``source`` is the tool name, kept for diagnostics only - nothing reads it to
    make a decision. It exists so that "why did the pass never run last night"
    has an answer in the data rather than a theory.
    """
    try:
        conn.execute(
            "UPDATE activity SET last_active_at = ?, source = ? WHERE id = 1",
            (utcnow_iso(), source),
        )
        conn.commit()
    except sqlite3.Error:  # a pre-v12 store, a locked DB, a read-only mount
        logger.debug("activity heartbeat failed for %r; continuing", source, exc_info=True)


def last_active(conn: sqlite3.Connection) -> datetime | None:
    """The instant the brain was last used, or ``None`` if unknown.

    ``None`` means the question cannot be answered - no heartbeat row, or a
    store that predates it. Callers must treat that as "do not assume idle"
    rather than as "idle forever"; see ``idle_seconds``.
    """
    try:
        row = conn.execute("SELECT last_active_at FROM activity WHERE id = 1").fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        parsed = datetime.fromisoformat(row[0])
    except (TypeError, ValueError):
        return None
    # Stored stamps are UTC-aware, but a hand-edited or imported row might not
    # be. Assume UTC rather than crashing the comparison in the caller.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def idle_seconds(conn: sqlite3.Connection) -> float | None:
    """Seconds since the brain was last used, or ``None`` if unknown.

    A negative interval is clamped to zero. Clock skew between two machines
    sharing a brain over the HTTP transport can stamp the future, and "the
    store was last used in ninety seconds" should read as *busy now*, which
    zero achieves and a negative number does not.
    """
    seen = last_active(conn)
    if seen is None:
        return None
    return max(0.0, (datetime.now(UTC) - seen).total_seconds())


def is_idle_for(conn: sqlite3.Connection, threshold_seconds: float) -> bool:
    """Has the brain gone untouched for at least ``threshold_seconds``?

    Returns ``False`` when the answer is unknown. That asymmetry is the whole
    point of the function: an unreadable heartbeat means we have no evidence
    the user is away, and starting background work on no evidence is the
    failure this table exists to prevent.
    """
    idle = idle_seconds(conn)
    if idle is None:
        return False
    return idle >= threshold_seconds
