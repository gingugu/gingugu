"""X7c in the hooks: a file's first read names the tables it uses, with where each is
defined, and the ones it defines, with who else uses them. An edit says so when it
starts using a table defined nowhere here (or one dropped here), when it changes a
table definition other files use, and when it drops a table other files still use.
``MEMORY_XRAY_TABLES=off`` silences all of it.
"""

from __future__ import annotations

import pytest

from gingugu.xray import edit_note, read_hook
from tests.test_xray_edit_hook import _payload, _repo
from tests.test_xray_tables_map import FILES


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_XRAY_TABLES", raising=False)
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "no-roots"))
    return tmp_path / "hook-sessions"


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


def _read(repo, rel, session="cc-1"):
    out = _payload(repo, rel, tool="Read", session=session)
    out["hook_event_name"] = "PostToolUse"
    return out


def _written(repo, rel, text, session="cc-1"):
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    out = _payload(repo, rel, tool="Write", session=session)
    out["hook_event_name"] = "PostToolUse"
    out["tool_input"]["content"] = text
    return out


def _edited(repo, rel, old, new, session="cc-1"):
    path = repo / rel
    path.write_text(path.read_text().replace(old, new))
    out = _payload(repo, rel, tool="Edit", session=session)
    out["hook_event_name"] = "PostToolUse"
    out["tool_input"].update(old_string=old, new_string=new)
    return out


def _tables(text):
    return next(ln for ln in (text or "").splitlines() if ln.startswith("Tables: "))


# --- the read hook -------------------------------------------------------------------


def test_a_first_read_names_the_tables_a_file_uses_and_where_each_is_defined(repo, state):
    text = read_hook.check(_read(repo, "app/repo.py"), state)
    line = _tables(text)
    assert line.startswith("Tables: uses users (defined in db/schema.sql:1)")
    assert "orders (defined in db/schema.sql:4)" in line
    assert "legacy_log (dropped in db/migrate_002.sql:1)" in line
    assert "ghost_table (defined nowhere here)" in line
    assert read_hook.check(_read(repo, "app/repo.py"), state) is None  # once


def test_a_defining_file_names_who_else_uses_each_table(repo, state):
    line = _tables(read_hook.check(_read(repo, "db/schema.sql"), state))
    assert line.startswith("Tables: defines users (used by app/repo.py, cmd/main.go +1)")
    assert "orders (used by app/repo.py, db/migrate_002.sql +1)" in line
    assert "docs_fts (used nowhere else here)" in line
    assert "(+1 more; code_xray tables has them all)" in line  # 5 defines, 4 shown
    assert "uses users" not in line  # its own tables are not news


def test_users_are_listed_code_before_tests(repo, state):
    from gingugu.xray import table_hook

    shown = table_hook._files(["tests/a.py", "z/b.py", "tests/a.py"])
    assert shown.startswith("z/b.py, tests/a.py")


def test_a_file_with_no_tables_has_no_tables_line(repo, state):
    assert "Tables: " not in (read_hook.check(_read(repo, "docs/notes.py"), state) or "")


def test_a_file_no_reader_handles_never_scans_the_repo(repo, state, monkeypatch):
    from gingugu.xray import table_hook, table_map

    def boom(*a, **k):
        raise AssertionError("a README needs no table scan")

    monkeypatch.setattr(table_map, "read", boom)
    assert table_hook.file_line(repo, "README.md", state) is None


def test_the_read_hook_stays_silent_on_a_partial_map(repo, state, monkeypatch):
    from gingugu.xray import table_hook

    monkeypatch.setattr(table_hook, "BUDGET_S", 0)
    assert table_hook.file_line(repo, "app/repo.py", state) is None  # not "defined nowhere"


# --- the edit note: uses -------------------------------------------------------------


def test_an_edit_using_a_table_defined_nowhere_says_so_once(repo, state):
    new = 'def more(conn):\n    conn.execute("SELECT 1 FROM usr")\n'
    out = _edited(repo, "app/repo.py", "def ghost(conn):\n", new + "def ghost(conn):\n")
    assert "Tables: uses usr, defined nowhere in this repo" in edit_note.check(out, state)
    again = _written(repo, "app/also.py", 'q = "SELECT 1 FROM usr"\n')
    assert "usr" not in (edit_note.check(again, state) or "")  # once a name a session


def test_an_edit_using_a_dropped_table_says_where_it_was_dropped(repo, state):
    out = _written(repo, "app/late.py", 'q = "SELECT 1 FROM legacy_log"\n')
    assert "Tables: uses legacy_log, dropped in db/migrate_002.sql:1" in edit_note.check(
        out, state
    )


def test_an_edit_using_a_defined_table_says_nothing_of_it(repo, state):
    out = _written(repo, "app/fine.py", 'q = "SELECT 1 FROM orders"\n')
    assert "orders" not in (edit_note.check(out, state) or "")


def test_a_table_used_before_the_edit_is_not_news(repo, state):
    out = _edited(repo, "app/repo.py", "import sqlite3\n", "import sqlite3  # touched\n")
    assert "ghost_table" not in (edit_note.check(out, state) or "")


def test_a_repo_defining_no_tables_never_says_defined_nowhere(tmp_path, state):
    other = _repo(tmp_path / "other", {"svc/a.py": 'q = "SELECT 1 FROM remote_t"\n'})
    out = _written(other, "svc/b.py", 'q = "SELECT 1 FROM remote_two"\n')
    assert "remote_two" not in (edit_note.check(out, state) or "")  # its schema lives elsewhere


# --- the edit note: defines and drops ------------------------------------------------


def test_an_edit_to_a_table_definition_names_how_many_files_use_it(repo, state):
    out = _edited(
        repo, "db/schema.sql",
        "CREATE TABLE orders (id INTEGER, user_id INTEGER);\n",
        "CREATE TABLE orders (id INTEGER, user_id INTEGER, note TEXT);\n",
    )  # fmt: skip
    text = edit_note.check(out, state)
    assert "Tables: defines orders, used by 3 other files (code_xray tables lists them)" in text
    again = _edited(
        repo, "db/schema.sql",
        "CREATE TABLE orders (id INTEGER, user_id INTEGER, note TEXT);\n",
        "CREATE TABLE orders (id INTEGER, user_id INTEGER, memo TEXT);\n",
    )  # fmt: skip
    assert "defines orders" not in (edit_note.check(again, state) or "")  # once a session


def test_an_edit_to_a_definition_nothing_else_uses_is_quiet(repo, state):
    out = _edited(
        repo, "db/schema.sql",
        "CREATE VIRTUAL TABLE docs_fts USING fts5(body);\n",
        "CREATE VIRTUAL TABLE docs_fts USING fts5(body, title);\n",
    )  # fmt: skip
    assert "docs_fts" not in (edit_note.check(out, state) or "")


def test_an_edit_dropping_a_table_still_used_says_who_uses_it(repo, state):
    out = _written(repo, "db/migrate_003.sql", "DROP TABLE orders;\n")
    text = edit_note.check(out, state)
    assert "Tables: drops orders, still used by app/repo.py, db/migrate_002.sql +2" in text


def test_a_drop_and_recreate_in_one_file_is_not_a_drop(repo, state):
    sql = "DROP TABLE IF EXISTS orders;\nCREATE TABLE orders (id INTEGER);\n"
    out = _written(repo, "db/migrate_003.sql", sql)
    assert "drops orders" not in (edit_note.check(out, state) or "")


def test_at_most_three_table_lines_an_edit(repo, state):
    sql = "".join(f'q{i} = "SELECT 1 FROM nowhere_{i}"\n' for i in range(6))
    text = edit_note.check(_written(repo, "app/many.py", sql), state)
    assert sum(ln.startswith("- Tables: ") for ln in text.splitlines()) == 3


# --- the switch -----------------------------------------------------------------------


def test_the_switch_off_silences_all_of_it(repo, state, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_TABLES", "off")
    assert "Tables: " not in (read_hook.check(_read(repo, "app/repo.py"), state) or "")
    out = _written(repo, "db/migrate_003.sql", "DROP TABLE orders;\n")
    assert "Tables: " not in (edit_note.check(out, state) or "")
