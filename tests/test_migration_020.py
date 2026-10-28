"""Migration 020: ``board_posts.author_agent`` and ``recipient_agent``.

Several sessions of one persona can run on one machine. Without an agent
session, a reply meant for the session that asked lands in every session of
that persona. Both columns are nullable and never backfilled: an old post's
session is unknown, and unknown keeps the old rule - every session sees it.
"""

from __future__ import annotations

import sqlite3
import uuid

from gingugu.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS, migrate
from gingugu.models import utcnow_iso


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _apply_through(conn: sqlite3.Connection, version: int) -> None:
    for target, fn in MIGRATIONS:
        if target > version:
            break
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
    conn.commit()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(board_posts)")}


def test_020_is_registered_once():
    targets = [t for t, _ in MIGRATIONS]
    assert targets.count(20) == 1
    assert LATEST_SCHEMA_VERSION >= 20


def test_fresh_db_has_both_agent_columns():
    conn = _conn()
    migrate(conn)
    assert {"author_agent", "recipient_agent"} <= _columns(conn)


def test_upgrade_from_19_keeps_posts_with_unknown_agents():
    conn = _conn()
    _apply_through(conn, 19)
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'ns', ?, ?)",
        (ns_id, now, now),
    )
    conn.execute(
        "INSERT INTO board_posts(id, namespace_id, parent_id, author, author_session, "
        "author_origin, recipient, kind, status, claimed_by, body, created_at, updated_at) "
        "VALUES ('p1', ?, NULL, 'tyrone', NULL, NULL, 'beepboop', 'message', 'open', NULL, "
        "'ahoy', ?, ?)",
        (ns_id, now, now),
    )
    conn.commit()
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    row = conn.execute(
        "SELECT body, author_agent, recipient_agent FROM board_posts WHERE id = 'p1'"
    ).fetchone()
    assert row["body"] == "ahoy"
    assert row["author_agent"] is None and row["recipient_agent"] is None


def test_020_is_idempotent_through_migrate():
    conn = _conn()
    migrate(conn)
    assert migrate(conn) == LATEST_SCHEMA_VERSION


def _at_19_with(conn: sqlite3.Connection, *columns: str) -> None:
    """A store a crash left at version 19 with some of 020's columns added."""
    _apply_through(conn, 19)
    for column in columns:
        conn.execute(f"ALTER TABLE board_posts ADD COLUMN {column} TEXT")
    conn.commit()


def test_020_resumes_after_a_crash_between_its_two_alters():
    # DDL autocommits: a failure after the first ALTER left the column and v19.
    conn = _conn()
    _at_19_with(conn, "author_agent")
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    assert {"author_agent", "recipient_agent"} <= _columns(conn)


def test_020_resumes_after_a_crash_before_the_version_stamp():
    conn = _conn()
    _at_19_with(conn, "author_agent", "recipient_agent")
    assert migrate(conn) == LATEST_SCHEMA_VERSION
