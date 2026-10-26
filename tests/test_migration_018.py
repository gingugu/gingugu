"""Migration 018: ``board_posts.author_origin``, the machine a post was written on.

A persona can run on more than one machine. Without the origin, a post one
machine's Beepboop leaves for ``beepboop`` reads as the reader's own post on
the other machine and never arrives. The column is nullable and never
backfilled: an old post's origin is unknown, and unknown keeps the old rule.
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


def test_018_is_the_latest_and_registered_once():
    targets = [t for t, _ in MIGRATIONS]
    assert targets.count(18) == 1
    assert LATEST_SCHEMA_VERSION == 18


def test_fresh_db_has_author_origin():
    conn = _conn()
    migrate(conn)
    assert "author_origin" in _columns(conn)


def test_upgrade_from_17_keeps_posts_with_an_unknown_origin():
    conn = _conn()
    _apply_through(conn, 17)
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'ns', ?, ?)",
        (ns_id, now, now),
    )
    conn.execute(
        "INSERT INTO board_posts(id, namespace_id, parent_id, author, author_session, recipient, "
        "kind, status, claimed_by, body, created_at, updated_at) "
        "VALUES ('p1', ?, NULL, 'beepboop', NULL, '*', 'message', 'open', NULL, 'ahoy', ?, ?)",
        (ns_id, now, now),
    )
    conn.commit()
    assert migrate(conn) == 18
    row = conn.execute("SELECT body, author_origin FROM board_posts WHERE id = 'p1'").fetchone()
    assert row["body"] == "ahoy" and row["author_origin"] is None


def test_018_is_idempotent_through_migrate():
    conn = _conn()
    migrate(conn)
    assert migrate(conn) == 18
