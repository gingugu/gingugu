"""Atomic multi-write transactions across components sharing one connection.

Most write paths here are single statements that commit themselves, which is
right: a store call should be durable when it returns. But a few operations are
*compound* - ``memory_consolidate`` creates a memory, writes N ``supersedes``
edges, and retires N originals - and a partial application of those is worse
than none at all. With ``keep_originals=False`` the retirement is a hard delete,
so a failure halfway through the loop destroys memories the surviving record
never absorbed.

``atomic()`` closes a gate on each participant so its internal ``commit()``
calls become no-ops, wraps the whole block in one ``BEGIN IMMEDIATE``, and
commits once at the end - or rolls the entire thing back.

**Deferred side effects.** Embeddings are deliberately *not* part of the
transaction. ``embedding_sync`` is best-effort by design (an encode failure
logs and moves on), and that must not change: a model hiccup may never roll
back real memories. Writing a vector inside the transaction would also strand
an orphan row if the block later aborts. So participants route those writes
through ``_after_commit``, which queues them while the gate is closed and runs
them once the data is durable.

``atomic()`` does not nest - a nested call raises rather than silently
degrading the inner block into a no-op savepoint.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class TransactionParticipant:
    """Write-side mixin: routes commits through a gate ``atomic()`` can close.

    Subclasses must hold their connection on ``self._conn`` and call
    ``TransactionParticipant.__init__(self)`` before any write.
    """

    _conn: sqlite3.Connection

    def __init__(self) -> None:
        self._suppress_commit = False
        self._deferred: list[Callable[[], None]] = []

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def _commit(self) -> None:
        """Commit, unless an enclosing ``atomic()`` owns the transaction."""
        if not self._suppress_commit:
            self._conn.commit()

    def _after_commit(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` now, or once the enclosing ``atomic()`` block commits."""
        if self._suppress_commit:
            self._deferred.append(fn)
        else:
            fn()


def _drain(participants: tuple[TransactionParticipant, ...]) -> None:
    """Run queued post-commit side effects. Best-effort, never raises.

    The data is already durable by this point; these are embeddings. Letting one
    encode failure escape would report a committed consolidation as failed.
    """
    for participant in participants:
        pending, participant._deferred = participant._deferred, []
        for fn in pending:
            try:
                fn()
            except Exception:
                logger.exception("deferred post-commit side effect failed; continuing")


@contextmanager
def atomic(*participants: TransactionParticipant) -> Iterator[None]:
    """Run a compound write as one transaction over shared participants."""
    if not participants:
        raise ValueError("atomic() requires at least one participant")
    conn = participants[0].conn
    if any(p.conn is not conn for p in participants):
        raise ValueError("atomic() participants must share one connection")
    if any(p._suppress_commit for p in participants):
        raise RuntimeError("atomic() blocks do not nest")

    # Flush any implicit transaction the caller left open: SQLite rejects a
    # BEGIN issued inside one.
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    for participant in participants:
        participant._suppress_commit = True
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        for participant in participants:
            participant._deferred.clear()
        raise
    finally:
        for participant in participants:
            participant._suppress_commit = False
    _drain(participants)
