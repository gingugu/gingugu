"""Every tool that acts on a memory records the act, tied to the query behind it.

A fetch by id (``memory_search ids``), an excerpt, an update, a relation (both
ends) and a forget each write a ``query_acts`` row; ``memory_recall`` logs the
memories it returned beside its ranked hits (related extras) so an act on one
of those still finds its query.
"""

from __future__ import annotations

import json
import sqlite3

import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "qa.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    monkeypatch.setattr("gingugu.storage_derived.current_session_id", lambda: "mcp-1")
    from gingugu.server import build_server

    return build_server(), tmp_path / "qa.db"


async def _call(srv, tool: str, **kwargs) -> dict:
    return json.loads((await srv.call_tool(tool, kwargs)).content[0].text)


async def _seed(srv, title: str) -> str:
    out = await _call(
        srv, "memory_store", title=title, content=title, type="fact", namespace="gingugu"
    )
    return out["memory"]["id"]


def _acts(path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT a.act, a.memory_id, q.query, a.rank, a.session_id FROM query_acts a "
            "LEFT JOIN query_log q ON q.id = a.query_id ORDER BY a.rowid"
        ).fetchall()
    finally:
        conn.close()


@pytest.fixture
async def recalled(server):
    srv, path = server
    hit = await _seed(srv, "ledger replay drained the exporter queue")
    other = await _seed(srv, "unrelated tuna sandwich")
    await _call(srv, "memory_recall", query="ledger replay exporter", namespace="gingugu")
    return srv, path, hit, other


async def test_a_fetch_by_id_names_its_query_and_rank(recalled):
    srv, path, hit, _ = recalled
    await _call(srv, "memory_search", ids=hit)
    assert _acts(path) == [("fetch", hit, "ledger replay exporter", 1, "mcp-1")]


async def test_an_update_is_an_act(recalled):
    srv, path, hit, _ = recalled
    await _call(srv, "memory_update", memory_id=hit, confidence="verified")
    assert _acts(path)[0][:3] == ("update", hit, "ledger replay exporter")


async def test_a_relation_is_an_act_on_both_ends(recalled):
    srv, path, hit, other = recalled
    await _call(srv, "memory_relate", source_id=hit, target_id=other, relation_type="caused_by")
    acts = _acts(path)
    assert {(a[0], a[1]) for a in acts} == {("relate", hit), ("relate", other)}
    by_id = {a[1]: a for a in acts}
    assert by_id[hit][2] == "ledger replay exporter"


async def test_an_excerpt_is_an_act(recalled):
    srv, path, hit, _ = recalled
    await _call(srv, "memory_excerpt", memory_id=hit, query="replay")
    assert _acts(path)[0][:2] == ("excerpt", hit)


async def test_a_forget_is_an_act(recalled):
    srv, path, hit, _ = recalled
    await _call(srv, "memory_forget", memory_id=hit)
    assert _acts(path)[0][:3] == ("forget", hit, "ledger replay exporter")


async def test_a_failed_update_records_nothing(recalled):
    srv, path, _, _ = recalled
    out = await _call(srv, "memory_update", memory_id="nope", confidence="verified")
    assert out["ok"] is False
    assert _acts(path) == []


async def test_recall_logs_related_extras_and_an_act_on_one_finds_its_query(server):
    srv, path = server
    hit = await _seed(srv, "ledger replay drained the exporter queue")
    near = await _seed(srv, "quarterly tuna sandwich budget")
    await _call(srv, "memory_relate", source_id=hit, target_id=near, relation_type="caused_by")
    await _call(
        srv,
        "memory_recall",
        query="ledger replay exporter",
        namespace="gingugu",
        include_related=True,
        limit=1,
    )
    conn = sqlite3.connect(path)
    hits, extras = conn.execute(
        "SELECT result_ids, extra_ids FROM query_log WHERE tool = 'recall'"
    ).fetchone()
    conn.close()
    assert near not in json.loads(hits)
    assert near in json.loads(extras)
    await _call(srv, "memory_search", ids=near)
    fetch = [a for a in _acts(path) if a[0] == "fetch"]
    assert fetch == [("fetch", near, "ledger replay exporter", None, "mcp-1")]
