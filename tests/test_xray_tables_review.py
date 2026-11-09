"""X7c review fixes, measured on a real checkout and from the security review.

Real repos showed noise the first spec could not: lowercase DDL in .sql files,
template strings and Dockerfile lines in test strings, and tests dropping tables
on purpose. The security review found a false 'defined nowhere' when a file is too
big to read, a cache with no scanner version, an edit scanned four times, a cache
written past what it can read back, and uncapped cached line numbers.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import edit_note, file_facts, table_hook, table_map, table_scan, table_tool
from tests.test_xray_edit_hook import _repo
from tests.test_xray_tables_hooks import _written
from tests.test_xray_tables_map import FILES


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_XRAY_TABLES", raising=False)
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "no-roots"))
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    return tmp_path / "hook-sessions"


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


# --- the scanner, on real-repo shapes ---------------------------------------------


def test_a_sql_file_is_read_in_any_case():
    # Uppercase-only keeps Python imports and prose out; a .sql file has neither.
    sql = (
        "create table if not exists t_one (id int);\n"
        "Create View v_one as select * from t_one join t_two on 1;\n"
        "alter table t_old rename to t_new;\n"
        "drop table t_gone;\n"
        "create index i on t_one(id);\n"
        "insert into t_one values (1);\n"
    )
    assert table_map.scan_text("db/schema.sql", sql) == (
        [("t_one", 1), ("v_one", 2), ("t_new", 3)],
        [("t_old", 3), ("t_gone", 4)],
        [("t_one", 2), ("t_two", 2), ("t_one", 5), ("t_one", 6)],
    )


def test_code_still_needs_uppercase_keywords():
    assert table_map.scan_text("a.py", 'q = "create table t_one (id int)"\n')[0] == []


@pytest.mark.parametrize(
    "rel, text",
    [
        ("a.py", 'q = f"SELECT 1 FROM t_{n}"\n'),  # a template: the name is built at run time
        ("a.ts", "const q = `SELECT 1 FROM t_${n}`;\n"),
        ("a.py", 'img = "FROM ghcr.io/acme/base:1"\n'),  # a Dockerfile line in a string
        ("a.py", 'img = "FROM python:3.12"\n'),
        ("a.py", 'img = "FROM base-py AS build"\n'),
    ],
)
def test_a_name_that_does_not_end_where_an_identifier_ends_is_not_a_table(rel, text):
    assert table_map.scan_text(rel, text)[2] == []


def test_a_sql_comment_still_ends_a_name():
    assert table_map.scan_text("a.sql", "SELECT 1 FROM t_one-- note\n")[2] == [("t_one", 1)]


def test_a_drop_in_a_test_file_is_not_a_drop(tmp_path, state):
    repo = _repo(
        tmp_path,
        {
            "db/schema.sql": "CREATE TABLE t_one (id INT);\n",
            "app/a.py": 'q = "SELECT 1 FROM t_one"\n',
            "tests/test_a.py": 'db.execute("DROP TABLE t_one")\n',  # simulating a failure
        },
    )
    m = table_map.read(repo, state)
    assert m.drops["t_one"] == [("tests/test_a.py", 1)]
    assert table_map.dropped(m, "t_one") == []


# --- a file too big to read never makes a table 'defined nowhere' -----------------

BIG = {
    "db/big.sql": "CREATE TABLE users (id INT);\nCREATE TABLE notes (id INT);\n" + "-- pad\n" * 60,
    "db/small.sql": "CREATE TABLE tags (id INT);\n",
    "app/a.py": 'q = "SELECT 1 FROM users JOIN tags"\n',
}


@pytest.fixture
def big(tmp_path, monkeypatch):
    monkeypatch.setattr(table_map, "MAX_FILE_BYTES", 200)
    return _repo(tmp_path / "big", BIG)


def test_a_file_too_big_to_read_is_named_unread(big, state):
    m = table_map.read(big, state)
    assert m.unread == ("db/big.sql",)
    assert "users" not in m.defines and m.defines["tags"] == [("db/small.sql", 1)]


def test_a_file_cut_at_the_name_cap_is_named_unread(big, state, monkeypatch):
    monkeypatch.setattr(table_map, "MAX_FILE_BYTES", 262_144)
    monkeypatch.setattr(table_map, "MAX_NAMES", 1)
    cut = ("app/a.py", "db/big.sql")  # two uses / two defines each, one kept
    assert table_map.read(big, state).unread == cut
    assert table_map.read(big, state).unread == cut  # and still, from the cache


def test_with_a_file_unread_the_read_hook_never_says_defined_nowhere(big, state):
    line = table_hook.file_line(big, "app/a.py", state) or ""
    assert "defined nowhere" not in line
    assert "tags (defined in db/small.sql:1)" in line
    assert "users (not in the files read; db/big.sql is too big)" in line


def test_with_a_file_unread_the_edit_note_never_says_defined_nowhere(big, state):
    out = _written(big, "app/b.py", 'q = "SELECT 1 FROM users"\n')
    assert "defined nowhere" not in (edit_note.check(out, state) or "")


def test_with_a_file_unread_the_tool_names_it_and_flags_softly(big, state):
    out = table_tool.answer(str(big))
    rows = {r["name"]: r for r in out["tables"]}
    assert rows["users"]["flag"] == "not in the files read"
    assert out["unread"] == ["db/big.sql"]
    assert out["totals"]["defined_nowhere"] == 0
    assert "db/big.sql" in out["text"]


def test_a_complete_read_has_nothing_unread(repo, state):
    assert table_map.read(repo, state).unread == ()
    assert table_tool.answer(str(repo))["unread"] == []


# --- the cache --------------------------------------------------------------------


def test_a_cache_from_another_scanner_version_is_rescanned(repo, state, monkeypatch):
    table_map.read(repo, state)
    seen = []
    real = table_map.scan_text
    monkeypatch.setattr(table_map, "scan_text", lambda r, t: seen.append(r) or real(r, t))
    monkeypatch.setattr(table_scan, "SCAN_VERSION", table_scan.SCAN_VERSION + 1)
    table_map.read(repo, state)
    assert "db/schema.sql" in seen


def test_a_cached_line_number_past_any_real_file_is_dropped(repo, state):
    table_map.read(repo, state)
    (cache,) = state.glob("xray-tables-*.json")
    raw = json.loads(cache.read_text())
    raw["files"]["db/schema.sql"]["defines"][0][1] = 10**12
    cache.write_text(json.dumps(raw))
    assert table_map.read(repo, state).defines["users"] == [("db/schema.sql", 1)]


def test_a_cache_past_the_read_cap_is_never_written(repo, state, monkeypatch):
    monkeypatch.setattr(file_facts, "MAX_CACHE_BYTES", 10)
    assert table_map.read(repo, state).defines["users"]
    assert list(state.glob("xray-tables-*.json")) == []


# --- the edit note's cost ---------------------------------------------------------


def test_an_edit_scans_each_text_once(monkeypatch):
    seen = []
    real = table_map.scan_text
    monkeypatch.setattr(table_map, "scan_text", lambda r, t: seen.append(t) or real(r, t))
    table_hook._candidates(['q = "SELECT 1 FROM t_new"'], ['q = "SELECT 1 FROM t_old"'], "a.py")
    assert sorted(seen) == ['q = "SELECT 1 FROM t_new"', 'q = "SELECT 1 FROM t_old"']


def test_an_edit_text_past_the_file_cap_is_not_scanned(monkeypatch):
    seen = []
    monkeypatch.setattr(table_map, "scan_text", lambda r, t: seen.append(t) or ([], [], []))
    huge = 'q = "SELECT 1 FROM t_one"\n' * (table_map.MAX_FILE_BYTES // 20)
    assert table_hook._candidates([huge], [], "a.py") == []
    assert seen == []
