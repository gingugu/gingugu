"""When the dream pass is allowed to run, and what stops it once started.

This is the whole scheduling design, and the point of it is that **there is no
daemon.** The OS scheduler already solved "run this every fifteen minutes", on
every platform, with restart-on-boot and no supervision to write. What it cannot
do is decide whether *now* is a good time. So the timer stays outside and the
judgment lives here: an OS-scheduled ``gingugu dream --if-idle`` opens the DB,
reads one row, and is gone in milliseconds unless the brain is genuinely unused.

A long-lived process would have bought the same behaviour in exchange for a PID
file, a restart policy, three platform-specific service definitions, and a class
of bug where the daemon is dead and nothing says so.

Two guards, and they answer different questions:

* **the idle gate** - is a person using this brain right now? Checked before
  starting, and again between passes, so returning to the keyboard stops a run
  already under way.
* **the lock** - is another pass already running? Checked once. Two runs are
  not dangerous, just wasteful and confusing in the logs.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from . import activity, dream, dream_lock
from .embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

# Outcomes. A skip is an ordinary result, not an error: the scheduled case is
# expected to skip far more often than it runs.
RAN = "ran"
SKIPPED_ACTIVE = "active"
SKIPPED_LOCKED = "locked"


def guarded_run(
    conn: sqlite3.Connection,
    *,
    embedder_factory: Callable[[], EmbeddingProvider | None] | None = None,
    namespace_id: str | None = None,
    idle_seconds: float | None = None,
    lease_seconds: int = dream_lock.DEFAULT_LEASE_SECONDS,
) -> dict:
    """Run the pass if the guards allow it. Returns ``{"outcome": ..., ...}``.

    ``idle_seconds`` is the gate. Pass a number for scheduled runs and the pass
    only proceeds after that much quiet; pass ``None`` for a hand-run, which
    means "I am asking for this deliberately, do not second-guess me". The lock
    applies either way, because two concurrent passes are wasteful no matter who
    asked for them.

    The same ``idle_seconds`` becomes the cancellation check between passes. One
    threshold serving both is intentional: a run that would not have *started*
    given the current activity should not *continue* either, and deriving the
    two from one number means they can never drift into disagreeing.

    The embedder arrives as a **factory**, called only once both guards pass. On
    a fifteen-minute schedule the overwhelmingly common outcome is a skip, and a
    skip should cost one SELECT rather than loading an embedding model to then
    throw it away.
    """
    if idle_seconds is not None and not activity.is_idle_for(conn, idle_seconds):
        observed = activity.idle_seconds(conn)
        logger.info("dream skipped: brain active (idle %.0fs < %.0fs)", observed or 0, idle_seconds)
        return {"outcome": SKIPPED_ACTIVE, "idle_seconds": observed}

    with dream_lock.held(conn, lease_seconds=lease_seconds) as token:
        if token is None:
            return {"outcome": SKIPPED_LOCKED}

        should_continue = None
        if idle_seconds is not None:
            threshold = idle_seconds

            def should_continue() -> bool:
                return activity.is_idle_for(conn, threshold)

        report = dream.run(
            conn,
            namespace_id=namespace_id,
            embedder=embedder_factory() if embedder_factory else None,
            should_continue=should_continue,
        )

    report["outcome"] = RAN
    return report
