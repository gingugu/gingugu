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


def test_019_is_registered_once():
    targets = [t for t, _ in MIGRATIONS]
    assert targets.count(19) == 1


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
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    assert _tables(conn) - before == {"xray_snapshots", "xray_files", "xray_edges"}
    assert conn.execute("SELECT COUNT(*) FROM namespaces").fetchone()[0] == 1


def test_the_xray_tables_carry_no_fts_triggers():
    conn = _conn()
    migrate(conn)
    sql = " ".join(
        r[0] or "" for r in conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger'")
    )
    assert "xray" not in sql


def test_namespace_delete_cascades_to_files_and_edges_and_edges_need_files():
    import pytest

    conn = _conn()
    migrate(conn)
    now, ns = utcnow_iso(), str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'n', ?, ?)",
        (ns, now, now),
    )
    conn.execute(
        "INSERT INTO xray_snapshots(id, namespace_id, fingerprint, graph_digest, "
        "extractor_version, commit_sha, file_count, edge_count, unresolved, dynamic, external, "
        "parse_errors, skipped, created_at, last_used_at) "
        "VALUES ('s', ?, 'f', 'g', 1, NULL, 2, 1, 0, 0, 0, '[]', '[]', ?, ?)",
        (ns, now, now),
    )
    for path in ("a.py", "b.py"):
        conn.execute(
            "INSERT INTO xray_files(snapshot_id, path, is_test, is_entry, rank) "
            "VALUES ('s', ?, 0, 0, 0.5)",
            (path,),
        )
    conn.execute("INSERT INTO xray_edges VALUES ('s', 'a.py', 'b.py', 'module', 1, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO xray_edges VALUES ('s', 'a.py', 'ghost.py', 'module', 1, 0)")
    conn.execute("DELETE FROM namespaces WHERE id = ?", (ns,))
    for table in ("xray_snapshots", "xray_files", "xray_edges"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
