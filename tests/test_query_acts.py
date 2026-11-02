"""Tests for ``query_acts``: what a session did with what a query returned.

The paraphrase question set labels a real query with the memory the session
then acted on. Measured 2026-11-02, that label could not be recovered after the
fact: ``access_log`` rows carry a long-lived MCP session id, updates and
relations carry none, and ``query_log`` kept only the main hits. So the act is
tied to its query when it happens: the newest query of the same session that
returned the memory, at the rank it returned it, or none.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gingugu import query_acts, query_log
from gingugu.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS, migrate
from gingugu.migrations.queries import _migration_022_query_acts


def _at(version: int) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for target, fn in MIGRATIONS:
        if target > version:
            break
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
    conn.commit()
    return conn


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    migrate(c)
    return c


def _acts(conn) -> list[dict]:
    cur = conn.execute(
        "SELECT session_id, act, memory_id, query_id, rank FROM query_acts ORDER BY rowid"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _query(conn, session, results, extras=(), text="q") -> str:
    query_log.record(
        conn,
        tool="recall",
        query=text,
        result_ids=list(results),
        namespaces=["ns"],
        session_id=session,
        extra_ids=list(extras),
    )
    return conn.execute("SELECT id FROM query_log ORDER BY rowid DESC LIMIT 1").fetchone()[0]


# --- Migration 022 -----------------------------------------------------------


def test_022_is_registered_and_latest():
    assert (22, _migration_022_query_acts) in MIGRATIONS
    assert LATEST_SCHEMA_VERSION == 22


def test_upgrade_from_21_adds_extra_ids_and_the_acts_table():
    old = _at(21)
    old.execute(
        "INSERT INTO query_log(id, session_id, tool, query, namespaces, result_ids, created_at) "
        "VALUES ('q0', 's', 'recall', 'x', 'ns', '[\"a\"]', '2026-11-01T00:00:00+00:00')"
    )
    assert migrate(old) == 22
    assert old.execute("SELECT extra_ids FROM query_log").fetchone()[0] == "[]"
    assert old.execute("SELECT COUNT(*) FROM query_acts").fetchone()[0] == 0
    names = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_query_acts_query", "idx_query_acts_created"} <= names


# --- query_log keeps the extras ----------------------------------------------


def test_query_log_records_extras_apart_from_ranked_hits(conn):
    _query(conn, "s", ["a", "b"], extras=["c", "a", "c"])
    hits, extras = conn.execute("SELECT result_ids, extra_ids FROM query_log").fetchone()
    assert json.loads(hits) == ["a", "b"]
    assert json.loads(extras) == ["c"]  # de-duplicated, and never repeats a ranked hit


# --- attribution -------------------------------------------------------------


def test_an_act_names_the_query_and_rank_that_surfaced_it(conn):
    qid = _query(conn, "s", ["a", "b", "c"])
    assert query_acts.record(conn, act="fetch", memory_ids=["c"], session_id="s") == 1
    assert _acts(conn) == [
        {"session_id": "s", "act": "fetch", "memory_id": "c", "query_id": qid, "rank": 3}
    ]


def test_the_newest_query_that_returned_it_wins(conn):
    _query(conn, "s", ["a"], text="first")
    newer = _query(conn, "s", ["b", "a"], text="second")
    _query(conn, "s", ["z"], text="third, never returned a")
    query_acts.record(conn, act="update", memory_ids=["a"], session_id="s")
    assert (_acts(conn)[0]["query_id"], _acts(conn)[0]["rank"]) == (newer, 2)


def test_an_extra_attributes_with_no_rank(conn):
    qid = _query(conn, "s", ["a"], extras=["r"])
    query_acts.record(conn, act="relate", memory_ids=["r"], session_id="s")
    assert (_acts(conn)[0]["query_id"], _acts(conn)[0]["rank"]) == (qid, None)


def test_another_sessions_query_never_claims_the_act(conn):
    _query(conn, "other", ["a"])
    query_acts.record(conn, act="fetch", memory_ids=["a"], session_id="s")
    assert _acts(conn)[0]["query_id"] is None


def test_an_act_no_query_surfaced_is_still_a_row(conn):
    query_acts.record(conn, act="forget", memory_ids=["a"], session_id="s")
    assert _acts(conn)[0]["query_id"] is None


def test_no_session_means_no_attribution(conn):
    _query(conn, None, ["a"])
    query_acts.record(conn, act="fetch", memory_ids=["a"], session_id=None)
    row = _acts(conn)[0]
    assert (row["session_id"], row["query_id"]) == (None, None)


def test_one_row_per_distinct_memory(conn):
    assert query_acts.record(conn, act="fetch", memory_ids=["a", "a", "", "b"], session_id="s") == 2


def test_an_unknown_act_is_refused(conn):
    with pytest.raises(ValueError):
        query_acts.record(conn, act="stare", memory_ids=["a"], session_id="s")


def test_record_never_raises_on_a_store_without_the_table():
    bare = sqlite3.connect(":memory:")
    assert query_acts.record(bare, act="fetch", memory_ids=["a"], session_id="s") == 0


def test_record_does_not_commit(tmp_path):
    path = tmp_path / "x.db"
    c = sqlite3.connect(path)
    migrate(c)
    query_acts.record(c, act="fetch", memory_ids=["a"], session_id="s")
    other = sqlite3.connect(path)
    assert other.execute("SELECT COUNT(*) FROM query_acts").fetchone()[0] == 0
    c.commit()
    assert other.execute("SELECT COUNT(*) FROM query_acts").fetchone()[0] == 1


def test_a_corrupt_result_list_is_skipped_not_fatal(conn):
    conn.execute(
        "INSERT INTO query_log(id, session_id, tool, query, namespaces, result_ids, extra_ids, "
        "created_at) VALUES ('bad', 's', 'recall', 'x', 'ns', 'not json', '[]', '9999')"
    )
    good = _query(conn, "s", ["a"])
    query_acts.record(conn, act="fetch", memory_ids=["a"], session_id="s")
    assert _acts(conn)[0]["query_id"] == good
