"""Migration 023: ``board_posts.claimed_origin`` and ``claimed_agent``.

A claim recorded only the persona, so a twin session of the same persona could
not tell its own claim from another session's - or from no claim at all. Both
columns are nullable and never backfilled: an old claim's session is unknown,
and unknown keeps the old rule.
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


def test_023_is_registered_once():
    targets = [t for t, _ in MIGRATIONS]
    assert targets.count(23) == 1
    assert LATEST_SCHEMA_VERSION >= 23


def test_fresh_db_has_both_claimer_columns():
    conn = _conn()
    migrate(conn)
    assert {"claimed_origin", "claimed_agent"} <= _columns(conn)


def test_upgrade_from_22_keeps_an_old_claim_with_an_unknown_claimer():
    conn = _conn()
    _apply_through(conn, 22)
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'ns', ?, ?)",
        (ns_id, now, now),
    )
    conn.execute(
        "INSERT INTO board_posts(id, namespace_id, parent_id, author, recipient, kind, status, "
        "claimed_by, body, created_at, updated_at) "
        "VALUES ('t1', ?, NULL, 'tyrone', '*', 'task', 'claimed', 'beepboop', 'fix it', ?, ?)",
        (ns_id, now, now),
    )
    conn.commit()
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    row = conn.execute(
        "SELECT claimed_by, claimed_origin, claimed_agent FROM board_posts WHERE id = 't1'"
    ).fetchone()
    assert row["claimed_by"] == "beepboop"
    assert row["claimed_origin"] is None and row["claimed_agent"] is None


def test_023_resumes_after_a_crash_between_its_two_alters():
    conn = _conn()
    _apply_through(conn, 22)
    conn.execute("ALTER TABLE board_posts ADD COLUMN claimed_origin TEXT")
    conn.commit()
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    assert {"claimed_origin", "claimed_agent"} <= _columns(conn)


def test_023_resumes_after_a_crash_before_the_version_stamp():
    conn = _conn()
    _apply_through(conn, 22)
    for column in ("claimed_origin", "claimed_agent"):
        conn.execute(f"ALTER TABLE board_posts ADD COLUMN {column} TEXT")
    conn.commit()
    assert migrate(conn) == LATEST_SCHEMA_VERSION
