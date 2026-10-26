"""Migration 019: the X-ray snapshot tables, outside ``memories``."""

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


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_019_is_the_latest_and_registered_once():
    targets = [t for t, _ in MIGRATIONS]
    assert targets.count(19) == 1
    assert LATEST_SCHEMA_VERSION == 19


def test_upgrade_from_018_adds_the_tables_and_keeps_memories():
    conn = _conn()
    for target, fn in MIGRATIONS:
        if target > 18:
            break
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
    now, ns = utcnow_iso(), str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'n', ?, ?)",
        (ns, now, now),
    )
    before = _tables(conn)
    assert migrate(conn) == 19
    assert _tables(conn) - before == {"xray_snapshots", "xray_files", "xray_edges"}
    assert conn.execute("SELECT COUNT(*) FROM namespaces").fetchone()[0] == 1


def test_the_xray_tables_carry_no_fts_triggers():
    conn = _conn()
    migrate(conn)
    sql = " ".join(
        r[0] or "" for r in conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger'")
    )
    assert "xray" not in sql
