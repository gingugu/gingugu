"""Tests for the activity heartbeat.

The heartbeat exists to answer one question - "is a person using this brain
right now" - and every test here is about a way that answer could be wrong in a
direction that matters. Wrong toward *busy* costs a skipped background pass.
Wrong toward *idle* starts unattended work while someone is typing, so the
asymmetry is asserted directly rather than left to the reader of the code.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from gingugu import activity
from gingugu.database import Database
from gingugu.models import utcnow_iso


def _set_last_active(db: Database, when: datetime) -> None:
    db.conn.execute("UPDATE activity SET last_active_at = ? WHERE id = 1", (when.isoformat(),))
    db.conn.commit()


def test_migration_seeds_a_heartbeat_row(db: Database) -> None:
    """A fresh store must not read as idle-forever."""
    row = db.conn.execute("SELECT last_active_at, source FROM activity").fetchone()
    assert row is not None
    assert row[1] == "migration"
    assert activity.idle_seconds(db.conn) is not None


def test_activity_table_holds_exactly_one_row(db: Database) -> None:
    """``CHECK (id = 1)`` is what makes the heartbeat an UPDATE, not an upsert."""
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO activity (id, last_active_at) VALUES (2, ?)", (utcnow_iso(),))


def test_stamp_advances_the_clock(db: Database) -> None:
    _set_last_active(db, datetime.now(UTC) - timedelta(hours=3))
    assert activity.idle_seconds(db.conn) > 3000

    activity.stamp(db.conn, "memory_store")

    assert activity.idle_seconds(db.conn) < 5
    assert db.conn.execute("SELECT source FROM activity").fetchone()[0] == "memory_store"


def test_is_idle_for_respects_the_threshold(db: Database) -> None:
    _set_last_active(db, datetime.now(UTC) - timedelta(minutes=30))

    assert activity.is_idle_for(db.conn, 20 * 60) is True
    assert activity.is_idle_for(db.conn, 45 * 60) is False


def test_unknown_activity_never_reads_as_idle(db: Database) -> None:
    """The load-bearing asymmetry: no evidence is not evidence of absence.

    A store whose heartbeat row is missing cannot say when it was last used.
    The tempting reading is "never used, therefore idle, therefore safe to work
    on". It is the opposite: we know nothing, and starting a background pass on
    nothing is exactly what this table was added to prevent.
    """
    db.conn.execute("DELETE FROM activity")
    db.conn.commit()

    assert activity.last_active(db.conn) is None
    assert activity.idle_seconds(db.conn) is None
    assert activity.is_idle_for(db.conn, 0) is False


def test_unparseable_stamp_reads_as_unknown_not_idle(db: Database) -> None:
    db.conn.execute("UPDATE activity SET last_active_at = 'not a timestamp' WHERE id = 1")
    db.conn.commit()

    assert activity.idle_seconds(db.conn) is None
    assert activity.is_idle_for(db.conn, 0) is False


def test_future_stamp_clamps_to_busy(db: Database) -> None:
    """Clock skew between two machines sharing a brain must read as busy.

    Two clients on the HTTP transport can disagree about the time. A stamp from
    the future would otherwise produce a negative idle interval, which compares
    as *more* idle than any threshold and would invite a pass to start during
    someone else's session.
    """
    _set_last_active(db, datetime.now(UTC) + timedelta(minutes=10))

    assert activity.idle_seconds(db.conn) == 0.0
    assert activity.is_idle_for(db.conn, 60) is False


def test_naive_timestamp_is_read_as_utc(db: Database) -> None:
    """An imported or hand-edited row without a zone must not crash the compare."""
    naive = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)
    db.conn.execute("UPDATE activity SET last_active_at = ? WHERE id = 1", (naive.isoformat(),))
    db.conn.commit()

    idle = activity.idle_seconds(db.conn)
    assert idle is not None
    assert 250 < idle < 350


def test_stamp_never_raises_on_a_store_without_the_table(db: Database) -> None:
    """A failed heartbeat must never take a tool call down with it."""
    db.conn.execute("DROP TABLE activity")
    db.conn.commit()

    activity.stamp(db.conn, "memory_recall")  # must not raise
    assert activity.is_idle_for(db.conn, 0) is False
