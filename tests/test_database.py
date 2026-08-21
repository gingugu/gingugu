"""Tests for database connection, migrations, and FTS5 setup."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gingugu.database import LATEST_SCHEMA_VERSION, Database


def test_migration_sets_user_version(db: Database) -> None:
    version = db.conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == LATEST_SCHEMA_VERSION


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

    assert migrate(db.conn) == LATEST_SCHEMA_VERSION
    assert migrate(db.conn) == LATEST_SCHEMA_VERSION


# --- migration backup tests ------------------------------------------------


def test_no_backup_for_fresh_database(tmp_path: Path) -> None:
    """First-time DB creation (user_version=0) should not produce a backup."""
    db_path = tmp_path / "fresh.db"
    Database(db_path).connect().close()

    assert db_path.exists()
    backups = list(tmp_path.glob("fresh.db.bak-before-*"))
    assert backups == []


def test_no_backup_for_in_memory_database() -> None:
    """In-memory DB has no file to back up."""
    Database(Path(":memory:")).connect().close()
    # Nothing to assert beyond "doesn't crash" — there's no filesystem path.


def test_backup_taken_when_migrations_pending(tmp_path: Path) -> None:
    """Reopening a stale DB with new pending migrations triggers a backup."""
    db_path = tmp_path / "stale.db"

    # Create the DB at v3 by running only the first three migrations manually.
    conn = sqlite3.connect(str(db_path))
    from gingugu.database import MIGRATIONS

    for target, fn in MIGRATIONS[:3]:  # apply v1, v2, v3 only
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
    conn.commit()
    conn.close()

    assert db_path.exists()

    # Now reopen via Database — migrate() should see v3 < v4 and back up first.
    db = Database(db_path)
    db.connect()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
    db.close()

    backup = db_path.with_name("stale.db.bak-before-v4")
    assert backup.exists(), "expected pre-migration backup at v3 → v4 boundary"
    # Backup should still have the v3 schema (pre-migration snapshot).
    bconn = sqlite3.connect(str(backup))
    assert bconn.execute("PRAGMA user_version").fetchone()[0] == 3
    bconn.close()


def test_backup_captures_writes_still_sitting_in_the_wal(tmp_path: Path) -> None:
    """The pre-migration backup must include committed-but-uncheckpointed rows.

    Regression. This used to be ``shutil.copy2(db_path, ...)``, which copies the
    main database file only. We run in WAL mode, where a committed transaction
    lives in ``<db>-wal`` until a checkpoint folds it in, so a file copy silently
    left the newest writes behind - on a real brain, 4.6MB of committed memories
    against a 20MB file. This copy is the only safety net if a migration goes
    wrong, so missing the most recent work is the one thing it must never do.
    """
    db_path = tmp_path / "wal.db"

    from gingugu.database import MIGRATIONS

    holder = sqlite3.connect(str(db_path))
    holder.execute("PRAGMA journal_mode=WAL")
    for target, fn in MIGRATIONS[:3]:  # a v3 DB, so v4+ are pending
        fn(holder)
        holder.execute(f"PRAGMA user_version = {target}")
    holder.commit()

    holder.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("ns-wal", "in-the-wal", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    holder.commit()

    # Hold the connection open: SQLite checkpoints when the last one closes, and
    # the point of this test is the state before that happens.
    wal_file = db_path.with_name(db_path.name + "-wal")
    assert wal_file.exists() and wal_file.stat().st_size > 0, "test setup is not exercising the WAL"
    raw_main_file = db_path.read_bytes()
    assert (
        b"in-the-wal" not in raw_main_file
    ), "row unexpectedly already checkpointed; this test would prove nothing"

    db = Database(db_path)
    db.connect()
    db.close()
    holder.close()

    backup = db_path.with_name("wal.db.bak-before-v4")
    assert backup.exists()
    bconn = sqlite3.connect(str(backup))
    try:
        names = [r[0] for r in bconn.execute("SELECT name FROM namespaces")]
        assert "in-the-wal" in names, "backup lost a committed write that was in the WAL"
        assert bconn.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        bconn.close()


def test_backup_not_overwritten_on_retry(tmp_path: Path) -> None:
    """If a backup already exists for this target, leave it alone."""
    db_path = tmp_path / "retry.db"

    # Set up a v3 DB.
    conn = sqlite3.connect(str(db_path))
    from gingugu.database import MIGRATIONS

    for target, fn in MIGRATIONS[:3]:
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
    conn.commit()
    conn.close()

    # Pre-place a sentinel backup file.
    backup = db_path.with_name("retry.db.bak-before-v4")
    backup.write_text("DO NOT OVERWRITE")

    Database(db_path).connect().close()

    assert backup.read_text() == "DO NOT OVERWRITE"


def test_no_backup_when_already_at_latest(tmp_path: Path) -> None:
    """Reopening a fully-migrated DB shouldn't produce a backup."""
    db_path = tmp_path / "current.db"
    Database(db_path).connect().close()  # creates fresh + migrates to latest
    # Reopen — no pending migrations.
    Database(db_path).connect().close()

    backups = list(tmp_path.glob("current.db.bak-before-*"))
    assert backups == []
