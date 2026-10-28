"""Migration 021: ``xray_files.lang`` and ``kind`` - X-ray reads more than Python.

Every snapshot stored before 021 was Python files, so the column defaults are
the truth for old rows and nothing is backfilled. Resumable like 020.
"""

from __future__ import annotations

import sqlite3
import uuid

from gingugu.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS, migrate
from gingugu.migrations.xray import _migration_021_xray_languages
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


def _columns(conn) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(xray_files)")}


def _seed_v20_snapshot(conn) -> None:
    now = utcnow_iso()
    ns, sid = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'p', ?, ?)",
        (ns, now, now),
    )
    conn.execute(
        "INSERT INTO xray_snapshots(id, namespace_id, fingerprint, graph_digest, "
        "extractor_version, commit_sha, file_count, edge_count, unresolved, dynamic, external, "
        "parse_errors, skipped, created_at, last_used_at) "
        "VALUES (?, ?, ?, ?, 1, NULL, 1, 0, 0, 0, 0, '[]', '[]', ?, ?)",
        (sid, ns, "a" * 64, "b" * 64, now, now),
    )
    conn.execute(
        "INSERT INTO xray_files(snapshot_id, path, is_test, is_entry, rank) "
        "VALUES (?, 'core.py', 0, 0, 1.0)",
        (sid,),
    )
    conn.commit()


def test_021_is_registered_once_and_latest():
    targets = [t for t, _ in MIGRATIONS]
    assert targets.count(21) == 1
    assert LATEST_SCHEMA_VERSION == 21


def test_fresh_db_has_lang_and_kind():
    conn = _conn()
    migrate(conn)
    assert {"lang", "kind"} <= _columns(conn)


def test_old_rows_read_as_python_files():
    conn = _conn()
    _apply_through(conn, 20)
    _seed_v20_snapshot(conn)
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    row = conn.execute("SELECT lang, kind FROM xray_files WHERE path = 'core.py'").fetchone()
    assert (row["lang"], row["kind"]) == ("python", "file")


def test_resumes_when_one_column_already_exists():
    conn = _conn()
    _apply_through(conn, 20)
    conn.execute("ALTER TABLE xray_files ADD COLUMN lang TEXT NOT NULL DEFAULT 'python'")
    conn.commit()
    _migration_021_xray_languages(conn)  # must not fail on the column already there
    assert {"lang", "kind"} <= _columns(conn)


def test_is_idempotent():
    conn = _conn()
    migrate(conn)
    _migration_021_xray_languages(conn)
    assert {"lang", "kind"} <= _columns(conn)
