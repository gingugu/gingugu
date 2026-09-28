"""Migration 014: `provenance` and `about`, and `about` joins the FTS5 index.

The path every existing store takes. A v13 brain carrying real rows must come
out with both columns NULL, every row still findable by its title and content,
and FTS triggers that keep all three indexed columns in lockstep afterwards.
"""

from __future__ import annotations

import sqlite3

import pytest

from gingugu.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS, migrate
from gingugu.migrations.fields import _migration_014_provenance_about


def _v13_with_rows() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for target, fn in MIGRATIONS:
        if target > 13:
            break
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES ('n', 'ns', 'x', 'x')"
    )
    for i, (title, content) in enumerate(
        (("ledger replay", "the exporter drained"), ("hook blocks", "merge step in place"))
    ):
        conn.execute(
            "INSERT INTO memories(id, namespace_id, type, title, content, source, "
            "created_at, updated_at, last_accessed) "
            "VALUES (?, 'n', 'fact', ?, ?, 'session 2026-07-24', 'x', 'x', 'x')",
            (f"m{i}", title, content),
        )
    conn.commit()
    return conn


def _match(conn: sqlite3.Connection, query: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT m.id FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid "
            "WHERE memories_fts MATCH ? ORDER BY m.id",
            (query,),
        )
    ]


def test_014_is_the_latest():
    assert LATEST_SCHEMA_VERSION == 14


def test_upgrade_adds_both_columns_null_and_keeps_source():
    conn = _v13_with_rows()
    assert migrate(conn) == 14
    rows = conn.execute("SELECT source, provenance, about FROM memories ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("session 2026-07-24", None, None)] * 2


def test_upgrade_keeps_every_row_findable_by_title_and_content():
    conn = _v13_with_rows()
    migrate(conn)
    assert _match(conn, "ledger") == ["m0"]
    assert _match(conn, "exporter") == ["m0"]
    assert _match(conn, "merge") == ["m1"]


def test_about_is_an_indexed_column_after_upgrade():
    conn = _v13_with_rows()
    migrate(conn)
    columns = [r[1] for r in conn.execute("SELECT * FROM pragma_table_info('memories_fts')")]
    assert columns == ["title", "content", "about"]


def test_triggers_keep_about_in_lockstep():
    conn = _v13_with_rows()
    migrate(conn)
    conn.execute("UPDATE memories SET about = 'bootstrapping' WHERE id = 'm1'")
    assert _match(conn, "bootstrapping") == ["m1"]

    conn.execute("UPDATE memories SET about = 'onboarding' WHERE id = 'm1'")
    assert _match(conn, "bootstrapping") == []
    assert _match(conn, "onboarding") == ["m1"]

    conn.execute(
        "INSERT INTO memories(id, namespace_id, type, title, content, about, "
        "created_at, updated_at, last_accessed) "
        "VALUES ('m2', 'n', 'fact', 't', 'c', 'onboarding', 'x', 'x', 'x')"
    )
    assert _match(conn, "onboarding") == ["m1", "m2"]

    conn.execute("DELETE FROM memories WHERE id = 'm1'")
    assert _match(conn, "onboarding") == ["m2"]
    # An external-content index that fell out of step fails this check.
    conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('integrity-check')")


def test_rerunning_after_an_interrupted_upgrade_does_not_fail():
    """If the process dies after the migration but before `user_version` is
    stamped, the next start runs it again over the columns it already added."""
    conn = _v13_with_rows()
    _migration_014_provenance_about(conn)
    _migration_014_provenance_about(conn)
    assert _match(conn, "ledger") == ["m0"]


@pytest.mark.parametrize("column", ["provenance", "about"])
def test_fresh_databases_get_the_columns_too(column):
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    names = [r[1] for r in conn.execute("SELECT * FROM pragma_table_info('memories')")]
    assert column in names
