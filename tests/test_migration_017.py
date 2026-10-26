"""Migration 017: the agent board's tables, and their lifecycle under deletes."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from gingugu.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS, migrate
from gingugu.models import utcnow_iso

POST_COLUMNS = {
    "id",
    "namespace_id",
    "parent_id",
    "author",
    "author_session",
    "recipient",
    "kind",
    "status",
    "claimed_by",
    "body",
    "created_at",
    "updated_at",
}


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


def _namespace(conn: sqlite3.Connection, name: str = "ns") -> str:
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (ns_id, name, now, now),
    )
    return ns_id


def _post(conn: sqlite3.Connection, ns_id: str, parent_id: str | None = None) -> str:
    now = utcnow_iso()
    post_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO board_posts(id, namespace_id, parent_id, author, author_session, recipient, "
        "kind, status, claimed_by, body, created_at, updated_at) "
        "VALUES (?, ?, ?, 'beepboop', NULL, '*', 'message', 'open', NULL, 'ahoy', ?, ?)",
        (post_id, ns_id, parent_id, now, now),
    )
    return post_id


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_017_is_the_latest_and_registered_once():
    targets = [t for t, _ in MIGRATIONS]
    assert targets.count(17) == 1
    assert LATEST_SCHEMA_VERSION == 17


def test_fresh_db_gets_board_tables():
    conn = _conn()
    migrate(conn)
    assert _columns(conn, "board_posts") == POST_COLUMNS
    assert _columns(conn, "board_reads") == {"post_id", "reader", "read_at"}


def test_upgrade_from_16_keeps_memories_and_adds_board():
    conn = _conn()
    _apply_through(conn, 16)
    ns_id = _namespace(conn)
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO memories(id, namespace_id, type, title, content, confidence, "
        "created_at, updated_at, last_accessed, access_count) "
        "VALUES ('m1', ?, 'fact', 't', 'c', 'verified', ?, ?, ?, 0)",
        (ns_id, now, now, now),
    )
    conn.commit()
    assert migrate(conn) == 17
    assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 1
    _post(conn, ns_id)
    assert conn.execute("SELECT count(*) FROM board_posts").fetchone()[0] == 1


def test_deleting_a_namespace_takes_its_board_with_it():
    conn = _conn()
    migrate(conn)
    ns_id = _namespace(conn)
    post_id = _post(conn, ns_id)
    conn.execute(
        "INSERT INTO board_reads(post_id, reader, read_at) VALUES (?, 'tyrone', ?)",
        (post_id, utcnow_iso()),
    )
    conn.execute("DELETE FROM namespaces WHERE id = ?", (ns_id,))
    assert conn.execute("SELECT count(*) FROM board_posts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM board_reads").fetchone()[0] == 0


def test_deleting_a_post_takes_its_replies_and_reads():
    conn = _conn()
    migrate(conn)
    ns_id = _namespace(conn)
    root = _post(conn, ns_id)
    _post(conn, ns_id, parent_id=root)
    conn.execute(
        "INSERT INTO board_reads(post_id, reader, read_at) VALUES (?, 'tyrone', ?)",
        (root, utcnow_iso()),
    )
    conn.execute("DELETE FROM board_posts WHERE id = ?", (root,))
    assert conn.execute("SELECT count(*) FROM board_posts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM board_reads").fetchone()[0] == 0


def test_a_reader_marks_a_post_read_once():
    conn = _conn()
    migrate(conn)
    post_id = _post(conn, _namespace(conn))
    conn.execute(
        "INSERT INTO board_reads(post_id, reader, read_at) VALUES (?, 'tyrone', ?)",
        (post_id, utcnow_iso()),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO board_reads(post_id, reader, read_at) VALUES (?, 'tyrone', ?)",
            (post_id, utcnow_iso()),
        )


def test_reply_to_a_missing_parent_is_refused():
    conn = _conn()
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _post(conn, _namespace(conn), parent_id="no-such-post")
