"""Tests for database connection, migrations, and FTS5 setup."""

from __future__ import annotations

from gingugu.database import Database


def test_migration_sets_user_version(db: Database) -> None:
    version = db.conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 3


def test_wal_and_foreign_keys_enabled(db: Database) -> None:
    fk = db.conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_core_tables_exist(db: Database) -> None:
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert {"namespaces", "memories", "memories_fts", "access_log"} <= names
    assert {"credential_services", "credential_fields"} <= names
    assert {"tags", "memory_tags", "relations"} <= names


def test_fts_triggers_exist(db: Database) -> None:
    rows = db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
    names = {r["name"] for r in rows}
    assert {"memories_ai", "memories_ad", "memories_au"} <= names


def test_migrate_is_idempotent(db: Database) -> None:
    from gingugu.database import migrate

    assert migrate(db.conn) == 3
    assert migrate(db.conn) == 3
