"""The involuntary-recall hook logs the prompts it searched on.

Those prompts are the user's own words for a thing, which is exactly the query
shape the paraphrase set exists to measure. The hook runs on every turn and
must never break one, so everything here is about logging being best-effort:
it writes when it can and is silent when it cannot.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gingugu import prompt_hook
from gingugu.database import Database


def _rows(path) -> list[dict]:
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute("SELECT tool, query, namespaces, result_ids, session_id FROM query_log")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        conn.close()


@pytest.fixture
def brain(tmp_path):
    path = tmp_path / "memories.db"
    database = Database(path)
    database.connect()
    database.close()
    return path


def test_log_prompt_writes_a_hook_row(brain):
    prompt_hook.log_prompt(brain, "cc-session", "wal checkpoint starves", ["m2", "m1"], ["crow"])
    (row,) = _rows(brain)
    assert row["tool"] == "hook"
    assert row["query"] == "wal checkpoint starves"
    assert json.loads(row["result_ids"]) == ["m2", "m1"]
    assert row["namespaces"] == "crow"
    assert row["session_id"] == "cc-session"


def test_log_prompt_on_a_missing_db_does_not_create_one(tmp_path):
    path = tmp_path / "nope.db"
    prompt_hook.log_prompt(path, "s", "q", [], ["crow"])
    assert not path.exists()


def test_log_prompt_on_an_unmigrated_db_is_silent(tmp_path):
    path = tmp_path / "old.db"
    sqlite3.connect(path).close()  # a file with no query_log table
    prompt_hook.log_prompt(path, "s", "q", [], ["crow"])  # must not raise


def test_log_prompt_while_the_db_is_write_locked_is_silent(brain, monkeypatch):
    monkeypatch.setattr(prompt_hook, "LOG_BUSY_TIMEOUT_S", 0.05)
    holder = sqlite3.connect(brain)
    holder.execute("BEGIN IMMEDIATE")
    try:
        prompt_hook.log_prompt(brain, "s", "q", [], ["crow"])  # must not raise or hang
    finally:
        holder.rollback()
        holder.close()
    assert _rows(brain) == []


class _FakeProvider:
    def encode(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


def test_a_prompt_that_surfaces_nothing_is_still_logged(brain, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_DB_PATH", str(brain))
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setattr("gingugu.embeddings.build_provider", lambda *a, **k: _FakeProvider())
    payload = {
        "prompt": "how does the checkpoint policy interact with long read transactions",
        "session_id": "cc-1",
        "cwd": "/tmp/gingugu",
    }
    assert prompt_hook.run(payload) == 0
    assert capsys.readouterr().out == ""  # nothing surfaced, nothing injected
    (row,) = _rows(brain)
    assert row["tool"] == "hook"
    assert "checkpoint policy" in row["query"]
    assert json.loads(row["result_ids"]) == []
    assert row["namespaces"] == "crow,gingugu"


def test_a_prompt_under_the_length_floor_is_not_logged(brain, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(brain))
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setattr("gingugu.embeddings.build_provider", lambda *a, **k: _FakeProvider())
    prompt_hook.run({"prompt": "yup", "session_id": "cc-1", "cwd": "/tmp/gingugu"})
    assert _rows(brain) == []


@pytest.mark.parametrize("name", ["we#ird.db", "we?ird.db", "per%20cent.db"])
def test_a_missing_db_with_uri_characters_is_never_created(tmp_path, name):
    path = tmp_path / name
    prompt_hook.log_prompt(path, "s", "q", [], ["crow"])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["we#ird.db", "we?ird.db", "per%20cent.db"])
def test_a_db_path_with_uri_characters_is_logged_to_the_right_file(tmp_path, name):
    path = tmp_path / name
    database = Database(path)
    database.connect()
    database.close()
    prompt_hook.log_prompt(path, "s", "wal checkpoint", [], ["crow"])
    assert [r["query"] for r in _rows(path)] == ["wal checkpoint"]


def test_the_readonly_sweep_connection_never_creates_a_file(tmp_path):
    from gingugu.recall_sweep import connect_readonly

    with pytest.raises(sqlite3.Error):
        connect_readonly(tmp_path / "we#ird.db").execute("SELECT 1")
    assert list(tmp_path.iterdir()) == []


def test_a_non_database_error_while_logging_is_swallowed(brain, monkeypatch):
    def boom(*_a, **_k):
        raise ValueError("not a sqlite error")

    monkeypatch.setattr("gingugu.query_log.record", boom)
    prompt_hook.log_prompt(brain, "s", "q", [], ["crow"])  # must not raise
