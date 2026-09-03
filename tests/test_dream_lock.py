"""Tests for the dream pass's single-instance lock.

The lock's job is narrow: stop two passes computing the same PageRank at once.
What the tests are really guarding is the failure mode on the other side - a
lock that outlives its holder and blocks the pass forever. Every case below is
either "excludes a second runner" or "recovers from a holder that vanished".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gingugu import dream_lock
from gingugu.database import Database


def _expire(db: Database, when: datetime) -> None:
    db.conn.execute("UPDATE dream_lock SET expires_at = ? WHERE id = 1", (when.isoformat(),))
    db.conn.commit()


def test_lock_starts_unheld(db: Database) -> None:
    """No row means no holder - the honest starting state, with no special case."""
    assert db.conn.execute("SELECT COUNT(*) FROM dream_lock").fetchone()[0] == 0


def test_acquire_then_second_acquire_is_refused(db: Database) -> None:
    first = dream_lock.acquire(db.conn)
    assert first is not None
    assert dream_lock.acquire(db.conn) is None


def test_release_frees_the_lock(db: Database) -> None:
    token = dream_lock.acquire(db.conn)
    dream_lock.release(db.conn, token)

    assert db.conn.execute("SELECT COUNT(*) FROM dream_lock").fetchone()[0] == 0
    assert dream_lock.acquire(db.conn) is not None


def test_expired_lock_is_taken_over(db: Database) -> None:
    """A holder killed mid-pass costs one window, not every future run."""
    dream_lock.acquire(db.conn)
    _expire(db, datetime.now(UTC) - timedelta(seconds=1))

    assert dream_lock.acquire(db.conn) is not None


def test_unparseable_expiry_is_taken_over(db: Database) -> None:
    """A lock nobody can read the expiry of is a lock nobody can ever release.

    Treating it as held forever would wedge the pass permanently on a single
    corrupt cell, which is a worse outcome than stealing a lock that may still
    be live - the only cost of the steal is duplicated arithmetic.
    """
    dream_lock.acquire(db.conn)
    db.conn.execute("UPDATE dream_lock SET expires_at = 'garbage' WHERE id = 1")
    db.conn.commit()

    assert dream_lock.acquire(db.conn) is not None


def test_release_does_not_steal_from_a_later_holder(db: Database) -> None:
    """An overrunning pass must not delete the lock a successor now owns.

    Without the ``holder = ?`` guard, a run that exceeded its lease would clear
    the row on its way out and hand a third process a lock two others believe
    they hold.
    """
    stale_token = dream_lock.acquire(db.conn)
    _expire(db, datetime.now(UTC) - timedelta(seconds=1))
    new_token = dream_lock.acquire(db.conn)
    assert new_token is not None

    dream_lock.release(db.conn, stale_token)

    row = db.conn.execute("SELECT holder FROM dream_lock WHERE id = 1").fetchone()
    assert row is not None
    assert row[0] == new_token


def test_held_context_manager_releases_on_exception(db: Database) -> None:
    """A pass that raises must not leave the lock behind for a full lease."""
    try:
        with dream_lock.held(db.conn) as token:
            assert token is not None
            raise RuntimeError("pass exploded")
    except RuntimeError:
        pass

    assert dream_lock.acquire(db.conn) is not None


def test_held_yields_none_when_busy_rather_than_raising(db: Database) -> None:
    """ "Someone else is dreaming" is an ordinary outcome, not an exception.

    A scheduled command that raised here would have a cron job mailing about it
    nightly, and a mailbox nobody reads is how a real failure goes unnoticed.
    """
    dream_lock.acquire(db.conn)

    with dream_lock.held(db.conn) as token:
        assert token is None
