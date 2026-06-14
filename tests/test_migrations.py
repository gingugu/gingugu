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
    assert final == 4
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4

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
    assert migrate(conn) == 4
    # Running again is a no-op (no error, stays at v4).
    assert migrate(conn) == 4
    conn.close()
