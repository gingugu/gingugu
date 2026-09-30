"""Tests for ``query_log``: what was asked of the brain, and what came back.

The paraphrase question set needs real queries paired with what they returned,
and ``access_log`` cannot supply them: it keeps one row per *returned* memory,
so it never saw the question, and a query that found nothing left no trace at
all. Those zero-hit queries are the misses the set exists to measure, so the
property asserted most here is that an empty result is still a row.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gingugu import query_log
from gingugu.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS, migrate
from gingugu.migrations.queries import _migration_015_query_log


def _rows(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT tool, query, namespaces, result_ids, session_id, created_at "
        "FROM query_log ORDER BY created_at, rowid"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


# --- Migration 015 -----------------------------------------------------------


def _at(version: int) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for target, fn in MIGRATIONS:
        if target > version:
            break
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
    conn.commit()
    return conn


def test_015_is_registered():
    assert (15, _migration_015_query_log) in MIGRATIONS


def test_upgrade_from_14_creates_an_empty_query_log():
    conn = _at(14)
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0] == 0


def test_query_log_has_a_created_at_index():
    conn = _at(15)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_query_log_created" in names


# --- query_log.record --------------------------------------------------------


@pytest.fixture
def conn():
    c = _at(15)
    yield c
    c.close()


def test_record_writes_the_query_and_ranked_results(conn):
    assert query_log.record(
        conn,
        tool="recall",
        query="how does the hook stay cheap",
        result_ids=["b", "a", "c"],
        namespaces=["crow", "gingugu"],
        session_id="s1",
    )
    (row,) = _rows(conn)
    assert row["tool"] == "recall"
    assert row["query"] == "how does the hook stay cheap"
    assert json.loads(row["result_ids"]) == ["b", "a", "c"]  # rank order, not sorted
    assert row["namespaces"] == "crow,gingugu"
    assert row["session_id"] == "s1"
    assert row["created_at"]


def test_a_zero_hit_query_is_still_a_row(conn):
    assert query_log.record(
        conn, tool="recall", query="nothing matches this", result_ids=[], namespaces=None
    )
    (row,) = _rows(conn)
    assert json.loads(row["result_ids"]) == []


def test_every_namespace_is_recorded_as_a_star(conn):
    query_log.record(conn, tool="search", query="q", result_ids=[], namespaces=None)
    assert _rows(conn)[0]["namespaces"] == "*"


def test_a_missing_session_is_stored_as_null(conn):
    query_log.record(conn, tool="search", query="q", result_ids=[], namespaces=None)
    assert _rows(conn)[0]["session_id"] is None


@pytest.mark.parametrize("blank", [None, "", "   \n\t"])
def test_a_blank_query_is_not_logged(conn, blank):
    assert not query_log.record(conn, tool="search", query=blank, result_ids=[], namespaces=None)
    assert _rows(conn) == []


def test_the_query_is_stripped_and_capped(conn):
    long = "  " + "x" * (query_log.MAX_QUERY_CHARS + 500) + "  "
    query_log.record(conn, tool="recall", query=long, result_ids=[], namespaces=None)
    assert _rows(conn)[0]["query"] == "x" * query_log.MAX_QUERY_CHARS


def test_duplicate_result_ids_keep_first_position(conn):
    query_log.record(conn, tool="recall", query="q", result_ids=["a", "b", "a"], namespaces=None)
    assert json.loads(_rows(conn)[0]["result_ids"]) == ["a", "b"]


def test_record_never_raises_on_a_store_without_the_table():
    old = _at(14)
    assert not query_log.record(old, tool="recall", query="q", result_ids=[], namespaces=None)


def test_record_does_not_commit(conn):
    query_log.record(conn, tool="recall", query="q", result_ids=[], namespaces=None)
    assert conn.in_transaction


# --- The MCP handlers --------------------------------------------------------


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "ql.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    from gingugu.server import build_server

    return build_server(), tmp_path / "ql.db"


async def _call(srv, tool: str, **kwargs) -> dict:
    return _payload(await srv.call_tool(tool, kwargs))


async def _seed(srv) -> str:
    out = await _call(
        srv,
        "memory_store",
        title="ledger replay drained the exporter",
        content="the ledger replay drained the exporter queue",
        type="fact",
        namespace="gingugu",
    )
    return out["memory"]["id"]


def _logged(db_path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        return _rows(conn)
    finally:
        conn.close()


async def test_recall_logs_its_query_and_hits(server):
    srv, path = server
    mid = await _seed(srv)
    await _call(srv, "memory_recall", query="ledger replay", namespace="gingugu")
    (row,) = _logged(path)
    assert (row["tool"], row["query"], row["namespaces"]) == ("recall", "ledger replay", "gingugu")
    assert json.loads(row["result_ids"])[0] == mid


async def test_recall_that_finds_nothing_is_logged(server):
    srv, path = server
    await _call(srv, "memory_recall", query="zzqx nothing anywhere")
    (row,) = _logged(path)
    assert json.loads(row["result_ids"]) == []


async def test_a_widened_recall_records_every_namespace(server):
    srv, path = server
    await _seed(srv)
    await _call(srv, "memory_namespaces", action="create", name="empty-ns")
    out = await _call(srv, "memory_recall", query="ledger replay", namespace="empty-ns")
    assert out.get("widened_from") == ["empty-ns"]
    assert _logged(path)[0]["namespaces"] == "*"


async def test_search_with_a_query_is_logged(server):
    srv, path = server
    await _seed(srv)
    await _call(srv, "memory_search", query="exporter", namespace="gingugu")
    (row,) = _logged(path)
    assert (row["tool"], row["query"]) == ("search", "exporter")


async def test_search_by_ids_and_listing_are_not_logged(server):
    srv, path = server
    mid = await _seed(srv)
    await _call(srv, "memory_search", ids=mid)
    await _call(srv, "memory_search", namespace="gingugu", sort_by="created")
    assert _logged(path) == []


async def test_context_logs_its_task_hint(server):
    srv, path = server
    await _seed(srv)
    await _call(srv, "memory_context", namespace="crow,gingugu", task_hint="replay the ledger")
    (row,) = _logged(path)
    assert (row["tool"], row["query"]) == ("context", "replay the ledger")
    assert row["namespaces"] == "crow,gingugu"


async def test_context_without_a_task_hint_is_not_logged(server):
    srv, path = server
    await _seed(srv)
    await _call(srv, "memory_context", namespace="gingugu")
    assert _logged(path) == []


async def test_stats_reports_query_log_rows(server):
    srv, _ = server
    await _call(srv, "memory_recall", query="anything at all")
    out = await _call(srv, "memory_stats")
    assert out["stats"]["query_log_rows"] == 1


# --- Retention ---------------------------------------------------------------


def test_pruning_the_access_log_never_touches_the_query_log(conn):
    from gingugu.stats import prune_access_log

    query_log.record(conn, tool="recall", query="q", result_ids=[], namespaces=None)
    conn.execute("UPDATE query_log SET created_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()
    prune_access_log(conn, force=True)
    assert len(_rows(conn)) == 1
