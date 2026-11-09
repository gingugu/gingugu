"""X7c: DB tables as edges - the file using a table, and the file defining it.

Nothing imports a table: the code running ``SELECT ... FROM orders`` and the
migration creating ``orders`` are invisible to each other in the import graph,
and a rename or drop on either side breaks silently. ``table_map`` reads table
names in known define, drop and use forms - names and lines only. Client side only.
"""

from __future__ import annotations

import pytest

from gingugu.xray import table_map
from tests.test_xray_edit_hook import _repo

FILES = {
    "db/schema.sql": (
        "CREATE TABLE IF NOT EXISTS users (\n"
        "  id INTEGER PRIMARY KEY\n"
        ");\n"
        "CREATE TABLE orders (id INTEGER, user_id INTEGER);\n"
        "CREATE INDEX idx_orders_user ON orders(user_id);\n"
        "CREATE VIRTUAL TABLE docs_fts USING fts5(body);\n"
        "CREATE TABLE legacy_log (id INTEGER);\n"
        "CREATE VIEW active_users AS SELECT * FROM users;\n"
    ),
    "db/migrate_002.sql": (
        "ALTER TABLE legacy_log RENAME TO audit_log;\n"
        "ALTER TABLE orders ADD COLUMN total REAL;\n"
        "DROP TABLE IF EXISTS scratch;\n"
    ),
    "app/repo.py": (
        "import sqlite3\n"
        "from app import db\n"
        "def get(conn, uid):\n"
        '    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,))\n'
        "def orders(conn):\n"
        '    return conn.execute("""\n'
        "        SELECT o.id FROM orders o\n"
        "        JOIN users u ON u.id = o.user_id\n"
        '    """)\n'
        "def log(conn):\n"
        '    conn.execute("INSERT INTO legacy_log (id) VALUES (1)")\n'
        "def bump(conn):\n"
        '    conn.execute("UPDATE orders SET total = 0")\n'
        "def ghost(conn):\n"
        '    conn.execute("DELETE FROM ghost_table")\n'
    ),
    "app/models.py": (
        "class Invoice(Base):\n"
        '    __tablename__ = "invoices"\n'
        'payments = Table("payments", metadata)\n'
    ),
    "alembic/versions/001_init.py": (
        "def upgrade():\n"
        '    op.create_table("shipments")\n'
        "def downgrade():\n"
        '    op.drop_table("shipments")\n'
    ),
    "web/api.ts": (
        'const q = "SELECT id FROM orders WHERE user_id = $1";\n'
        "await db.query(`INSERT INTO audit_log (id) VALUES (1)`);\n"
    ),
    "cmd/main.go": 'package main\nconst q = "SELECT name FROM users"\n',
    "tests/test_repo.py": (
        "def test_x(conn):\n"
        '    conn.execute("SELECT name FROM sqlite_master")\n'
        '    conn.execute("INSERT INTO users (id) VALUES (1)")\n'
    ),
    "README.md": "Run SELECT * FROM users to see them.\n",
    "docs/notes.py": '"""One UPDATE on the existing row, then FROM the start."""\n',
}


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


# --- pure: what one file says --------------------------------------------------
# scan_text(rel, text) -> (defines, drops, uses), each [(name, line)] in line order,
# names lowercased, one entry per name per line.


@pytest.mark.parametrize(
    "rel, text, defines, drops, uses",
    [
        ("a.sql", "CREATE TABLE t_one (id INT);\n", [("t_one", 1)], [], []),
        ("a.sql", "CREATE TABLE IF NOT EXISTS t_one (\n", [("t_one", 1)], [], []),
        ("a.sql", "CREATE TEMP TABLE t_one (id INT);\n", [("t_one", 1)], [], []),
        ("a.sql", "CREATE TEMPORARY TABLE t_one (id INT);\n", [("t_one", 1)], [], []),
        ("a.sql", "CREATE VIRTUAL TABLE t_fts USING fts5(b);\n", [("t_fts", 1)], [], []),
        ("a.sql", "CREATE VIEW v_one AS SELECT 1;\n", [("v_one", 1)], [], []),
        ("a.sql", 'CREATE TABLE "Quoted_T" (id INT);\n', [("quoted_t", 1)], [], []),
        ("a.sql", "CREATE TABLE `tick_t` (id INT);\n", [("tick_t", 1)], [], []),
        ("a.sql", "CREATE TABLE [brk_t] (id INT);\n", [("brk_t", 1)], [], []),
        ("a.sql", "CREATE TABLE public.t_one (id INT);\n", [("t_one", 1)], [], []),
        ("a.sql", "ALTER TABLE t_old RENAME TO t_new;\n", [("t_new", 1)], [("t_old", 1)], []),
        ("a.sql", "ALTER TABLE t_one ADD COLUMN c INT;\n", [], [], [("t_one", 1)]),
        ("a.sql", "DROP TABLE t_one;\n", [], [("t_one", 1)], []),
        ("a.sql", "DROP TABLE IF EXISTS t_one;\n", [], [("t_one", 1)], []),
        ("a.py", '__tablename__ = "t_one"\n', [("t_one", 1)], [], []),
        ("a.py", "t = Table('t_one', meta)\n", [("t_one", 1)], [], []),
        ("a.py", 'op.create_table("t_one")\n', [("t_one", 1)], [], []),
        ("a.py", 'op.drop_table("t_one")\n', [], [("t_one", 1)], []),
        ("a.py", 'q = "SELECT a FROM t_one"\n', [], [], [("t_one", 1)]),
        ("a.py", 'q = "INSERT INTO t_one (a) VALUES (1)"\n', [], [], [("t_one", 1)]),
        ("a.py", 'q = "INSERT OR REPLACE INTO t_one VALUES (1)"\n', [], [], [("t_one", 1)]),
        ("a.py", 'q = "UPDATE t_one SET a = 1"\n', [], [], [("t_one", 1)]),
        ("a.py", 'q = "DELETE FROM t_one"\n', [], [], [("t_one", 1)]),
        ("a.py", 'q = "SELECT 1 FROM t_one JOIN t_two"\n', [], [], [("t_one", 1), ("t_two", 1)]),
        ("a.py", 'q = "SELECT 1 FROM t_one JOIN T_ONE"\n', [], [], [("t_one", 1)]),  # once a line
        ("a.ts", "const q = `SELECT a FROM t_one`;\n", [], [], [("t_one", 1)]),
        ("a.go", 'q := "SELECT a FROM t_one"\n', [], [], [("t_one", 1)]),
        ("a.sql", "CREATE INDEX i ON t_one(a);\n", [], [], [("t_one", 1)]),
        ("a.sql", "CREATE UNIQUE INDEX i ON t_one(a);\n", [], [], [("t_one", 1)]),
        (
            "a.sql",
            "CREATE TRIGGER g AFTER INSERT ON t_one BEGIN INSERT INTO t_two VALUES (1); END;\n",
            [], [], [("t_one", 1), ("t_two", 1)],
        ),  # fmt: skip
    ],
)
def test_scan_text_finds_defines_drops_and_uses(rel, text, defines, drops, uses):
    assert table_map.scan_text(rel, text) == (defines, drops, uses)


@pytest.mark.parametrize(
    "text",
    [
        "from os import path\nfrom app import db\n",  # lowercase: Python, never SQL
        'q = "select a from t_one"\n',  # lowercase SQL is not read: prose looks the same
        '"""One UPDATE on the existing row, then FROM the start."""\n',  # SQL words
        'q = "SELECT a FROM (SELECT 1)"\n',  # a subquery names no table
        'q = "SELECT name FROM sqlite_master"\n',
        'q = "SELECT * FROM pragma_table_info(\'t\')"\n',
        'q = "SELECT * FROM information_schema.tables"\n',
        'q = "SELECT * FROM pg_catalog.pg_class"\n',
        'q = "SELECT * FROM pg_tables"\n',
        'q = "JOIN u ON u.id = o.uid"\n',  # ON outside CREATE INDEX/TRIGGER: no use
        'q = "SELECT 1 FROM x"\n',  # a one-letter name is an alias or a fixture
    ],
)
def test_what_is_not_a_table(text):
    assert table_map.scan_text("a.py", text)[2] == []


@pytest.mark.parametrize("rel", ["README.md", "notes.txt", "a.yml", "Dockerfile", ".env", "a.rb"])
def test_only_code_and_sql_files_are_read(rel):
    assert table_map.kind(rel) is None
    assert table_map.scan_text(rel, "CREATE TABLE t_one (id INT);\nSELECT 1 FROM t_one\n") == (
        [], [], [],
    )  # fmt: skip


@pytest.mark.parametrize(
    "rel", ["a.py", "a.sql", "a.SQL", "a.ts", "a.tsx", "a.js", "a.mjs", "a.go"]
)
def test_kinds_that_are_read(rel):
    assert table_map.kind(rel) is not None


def test_a_rename_is_not_also_a_use():
    assert table_map.scan_text("a.sql", "ALTER TABLE t_old RENAME TO t_new;\n")[2] == []


# --- the repo map -----------------------------------------------------------------


def test_read_maps_every_define_drop_and_use(repo, state):
    m = table_map.read(repo, state)
    assert m.defines["users"] == [("db/schema.sql", 1)]
    assert m.uses["users"] == [
        ("app/repo.py", 4), ("app/repo.py", 8), ("cmd/main.go", 2),
        ("db/schema.sql", 8), ("tests/test_repo.py", 3),
    ]  # fmt: skip
    assert m.uses["orders"] == [
        ("app/repo.py", 7), ("app/repo.py", 13), ("db/migrate_002.sql", 2),
        ("db/schema.sql", 5), ("web/api.ts", 1),
    ]  # fmt: skip
    assert m.defines["audit_log"] == [("db/migrate_002.sql", 1)]
    assert m.drops["legacy_log"] == [("db/migrate_002.sql", 1)]
    assert m.drops["scratch"] == [("db/migrate_002.sql", 3)]
    assert m.defines["invoices"] == [("app/models.py", 2)]
    assert m.defines["payments"] == [("app/models.py", 3)]
    assert m.defines["shipments"] == [("alembic/versions/001_init.py", 2)]
    assert m.drops["shipments"] == [("alembic/versions/001_init.py", 4)]
    assert m.uses["ghost_table"] == [("app/repo.py", 15)] and "ghost_table" not in m.defines
    assert "sqlite_master" not in m.uses and "on" not in m.uses and "the" not in m.uses
    assert m.complete is True


def test_names_in_lists_a_files_names_in_line_order(repo, state):
    m = table_map.read(repo, state)
    assert table_map.names_in(m.uses, "app/repo.py") == [
        "users", "orders", "legacy_log", "ghost_table",
    ]  # fmt: skip


def test_dropped_still_used_is_a_drop_in_a_file_that_does_not_define_it_again(repo, state):
    m = table_map.read(repo, state)
    assert table_map.dropped(m, "legacy_log") == [("db/migrate_002.sql", 1)]
    assert table_map.dropped(m, "shipments") == []  # its own downgrade: created there too
    assert table_map.dropped(m, "users") == []


def test_an_unchanged_file_is_not_rescanned(repo, state, monkeypatch):
    table_map.read(repo, state)
    seen = []
    real = table_map.scan_text
    monkeypatch.setattr(table_map, "scan_text", lambda r, t: seen.append(r) or real(r, t))
    (repo / "cmd/main.go").write_text('package main\nconst q = "SELECT 1 FROM brand_new"\n')
    m = table_map.read(repo, state)
    assert seen == ["cmd/main.go"]
    assert m.uses["brand_new"] == [("cmd/main.go", 2)]
    assert ("cmd/main.go", 2) not in m.uses["users"]


def test_a_file_past_the_byte_cap_is_skipped(repo, state, monkeypatch):
    monkeypatch.setattr(table_map, "MAX_FILE_BYTES", 16)
    assert "users" not in table_map.read(repo, state).defines


def test_a_corrupt_cache_is_rebuilt(repo, state):
    table_map.read(repo, state)
    (cache,) = state.glob("xray-tables-*.json")
    cache.write_text("{not json")
    assert table_map.read(repo, state).defines["users"]


def test_a_cache_entry_out_of_shape_is_dropped_and_rescanned(repo, state):
    import json

    table_map.read(repo, state)
    (cache,) = state.glob("xray-tables-*.json")
    raw = json.loads(cache.read_text())
    raw["files"]["db/schema.sql"]["defines"] = [["BAD NAME\n", 1]]
    cache.write_text(json.dumps(raw))
    assert table_map.read(repo, state).defines["users"] == [("db/schema.sql", 1)]


def test_the_cache_is_owner_only(repo, state):
    import os
    import stat
    import sys

    table_map.read(repo, state)
    (cache,) = state.glob("xray-tables-*.json")
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(cache).st_mode) & 0o077 == 0


def test_the_switch(monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_TABLES", "off")
    assert table_map.enabled() is False
    monkeypatch.setenv("MEMORY_XRAY_TABLES", "on")
    assert table_map.enabled() is True


# --- the X7a hardening, carried over ------------------------------------------------


def test_a_file_full_of_uses_scans_in_linear_time():
    import time

    text = 'q = "SELECT a FROM t_one"\n' * 15000
    t = time.monotonic()
    uses = table_map.scan_text("big.py", text)[2]
    assert time.monotonic() - t < 1.0 and len(uses) == 15000


def test_past_the_budget_a_cold_read_reaches_nothing_and_a_full_one_everything(repo, state):
    cold = table_map.read(repo, state, budget_s=0)
    assert cold.uses == {} and cold.complete is False
    full = table_map.read(repo, state)
    assert full.uses["users"] and full.complete is True


def test_past_the_budget_a_file_keeps_its_cached_entry(repo, state):
    table_map.read(repo, state)
    warm = table_map.read(repo, state, budget_s=0)
    assert warm.defines["users"] == [("db/schema.sql", 1)] and warm.complete is True


def test_a_cached_list_past_the_cap_is_rescanned(repo, state, monkeypatch):
    table_map.read(repo, state)
    monkeypatch.setattr(table_map, "MAX_NAMES", 0)
    (cache,) = state.glob("xray-tables-*.json")
    old, _ = table_map._load(cache)
    assert "db/schema.sql" not in old
