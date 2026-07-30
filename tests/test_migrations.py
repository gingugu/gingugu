"""Upgrade-migration tests — the path every existing user hits.

The suite elsewhere builds fresh (v0 -> v3) DBs; here we stand up an older
v2 database *with data* and prove migrate() carries it forward to v3 without
loss and creates the new tags/relations tables."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from gingugu.database import (
    _migration_001_initial_schema,
    _migration_002_credential_vault,
    migrate,
)
from gingugu.models import utcnow_iso


def _apply_through(conn: sqlite3.Connection, version: int) -> None:
    """Bring a bare connection up to `version` without running later migrations."""
    from gingugu.database import MIGRATIONS

    for target, fn in MIGRATIONS:
        if target > version:
            break
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
    conn.commit()


def _open_v2(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _migration_001_initial_schema(conn)
    _migration_002_credential_vault(conn)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    return conn


def _seed_v2(conn: sqlite3.Connection) -> tuple[str, str]:
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, path, description, created_at, updated_at) "
        "VALUES (?, 'legacy', NULL, NULL, ?, ?)",
        (ns_id, now, now),
    )
    mem_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO memories(id, namespace_id, type, title, content, confidence, source, "
        "created_at, updated_at, last_accessed, last_confirmed, access_count, metadata) "
        "VALUES (?, ?, 'fact', 'old memory', 'pre-existing content', 'verified', NULL, "
        "?, ?, ?, NULL, 0, NULL)",
        (mem_id, ns_id, now, now, now),
    )
    conn.commit()
    return ns_id, mem_id


def test_v2_to_v3_upgrade_preserves_data(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = _open_v2(path)
    ns_id, mem_id = _seed_v2(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2

    # Upgrade.
    final = migrate(conn)
    assert final == 5
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5

    # New tables exist.
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"tags", "memory_tags", "relations"}.issubset(tables)

    # Pre-existing data survived untouched.
    mem = conn.execute("SELECT title, content FROM memories WHERE id = ?", (mem_id,)).fetchone()
    assert mem["title"] == "old memory"
    assert mem["content"] == "pre-existing content"
    assert conn.execute("SELECT name FROM namespaces WHERE id = ?", (ns_id,)).fetchone()[0] == (
        "legacy"
    )
    conn.close()


def test_migrate_is_idempotent_when_current(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = _open_v2(path)
    _seed_v2(conn)
    assert migrate(conn) == 5
    # Running again is a no-op (no error, stays at v4).
    assert migrate(conn) == 5
    conn.close()


def test_v5_backfills_claims_for_existing_memories() -> None:
    """Migration 005 must POPULATE, not just create the table.

    Claims are otherwise written only on store/update, so every existing user
    would upgrade into an empty table and the feature would do nothing until
    they happened to edit a memory. Migration 004 solves the same shape with a
    startup backfill; claim extraction is pure regex (~210ms for 735 memories)
    so it belongs in the migration, where user_version guarantees exactly one run.
    """
    conn = sqlite3.connect(":memory:")  # deliberately NO row_factory
    _apply_through(conn, 4)
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("ns1", "gingugu", "2026-01-01", "2026-01-01"),
    )
    for mid, title, content in (
        ("m1", "PR #10 open", "PR #10, open, NOT merged yet"),
        ("m2", "released", "PR #10 merged to main"),
        ("m3", "no refs", "just some prose about tuning"),
    ):
        conn.execute(
            "INSERT INTO memories(id, namespace_id, type, title, content, confidence, "
            "created_at, updated_at, last_accessed, access_count) VALUES (?,?,?,?,?,?,?,?,?,0)",
            (
                mid,
                "ns1",
                "workflow",
                title,
                content,
                "verified",
                "2026-01-01",
                "2026-01-01",
                "2026-01-01",
            ),
        )

    assert migrate(conn) == 5

    rows = conn.execute(
        "SELECT memory_id, ref, state FROM memory_claims ORDER BY memory_id"
    ).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("m1", "gingugu#10", "open"),
        ("m2", "gingugu#10", "resolved"),
    ]  # m3 has no refs, so no rows - and that is not the same as "unprocessed"


def test_v5_backfill_runs_exactly_once() -> None:
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 4)
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("ns1", "gingugu", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO memories(id, namespace_id, type, title, content, confidence, "
        "created_at, updated_at, last_accessed, access_count) VALUES (?,?,?,?,?,?,?,?,?,0)",
        (
            "m1",
            "ns1",
            "workflow",
            "t",
            "PR #10 open",
            "verified",
            "2026-01-01",
            "2026-01-01",
            "2026-01-01",
        ),
    )
    migrate(conn)
    before = conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0]
    migrate(conn)  # user_version is already 5 - must not duplicate
    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == before == 1


def test_v5_on_a_fresh_database_is_a_no_op() -> None:
    conn = sqlite3.connect(":memory:")
    assert migrate(conn) == 5
    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == 0
