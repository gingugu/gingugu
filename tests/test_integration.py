"""End-to-end integration test: the full flow over the live MCP tool surface."""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    """Unwrap a FastMCP tool result into its JSON dict."""
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "e2e.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "e2e")
    from gingugu.server import build_server

    return build_server()


@pytest.mark.asyncio
async def test_full_flow(server) -> None:
    tools = {t.name for t in await server.list_tools()}
    assert {
        "memory_store",
        "memory_recall",
        "memory_context",
        "memory_update",
        "memory_forget",
        "memory_relate",
        "memory_consolidate",
        "memory_search",
        "memory_stats",
        "memory_namespaces",
        "memory_export",
        "memory_import",
    }.issubset(tools)

    # store -> two linked memories
    a = _payload(
        await server.call_tool(
            "memory_store",
            {
                "content": "use WAL mode for sqlite",
                "title": "wal",
                "type": "pattern",
                "tags": "sqlite",
                "confidence": "verified",
            },
        )
    )
    b = _payload(
        await server.call_tool(
            "memory_store",
            {
                "content": "busy_timeout avoids SQLITE_BUSY",
                "title": "busy",
                "type": "pattern",
                "tags": "sqlite",
            },
        )
    )
    assert a["ok"] and b["ok"]
    aid, bid = a["memory"]["id"], b["memory"]["id"]

    # recall finds it
    rec = _payload(await server.call_tool("memory_recall", {"query": "wal mode"}))
    assert rec["count"] >= 1

    # context surfaces something on session start, with tags populated
    # (regression: memory_context must load_tags like memory_recall does)
    ctx = _payload(await server.call_tool("memory_context", {}))
    assert ctx["ok"]
    assert any(m["tags"] for m in ctx["memories"]), "context results should carry tags"

    # relate + traversal
    rel = _payload(
        await server.call_tool(
            "memory_relate", {"source_id": aid, "target_id": bid, "relation_type": "related_to"}
        )
    )
    assert rel["ok"]
    rec2 = _payload(
        await server.call_tool("memory_recall", {"query": "wal mode", "include_related": True})
    )
    assert any(m.get("via_relation") for m in rec2["memories"])

    # search by tag
    srch = _payload(await server.call_tool("memory_search", {"tags": "sqlite"}))
    assert srch["count"] == 2

    # namespaces list reflects the e2e namespace
    ns = _payload(await server.call_tool("memory_namespaces", {"action": "list"}))
    assert any(n["name"] == "e2e" for n in ns["namespaces"])

    # export -> import into a second namespace-free payload roundtrip
    exp = _payload(await server.call_tool("memory_export", {}))
    assert len(exp["export"]["memories"]) == 2
    imp = _payload(
        await server.call_tool("memory_import", {"data": exp["export"], "on_conflict": "skip"})
    )
    assert imp["ok"]
    assert imp["memories_skipped"] == 2  # already present

    # stats with opt-in flag_stale (nothing aged yet -> 0)
    stats = _payload(await server.call_tool("memory_stats", {"flag_stale": True}))
    assert stats["ok"]
    assert stats["flagged_stale"] == 0
    assert stats["stats"]["total_memories"] == 2

    # forget (deprecate) then confirm it drops from default recall
    frg = _payload(await server.call_tool("memory_forget", {"memory_id": bid}))
    assert frg["action"] == "deprecated"
    rec3 = _payload(await server.call_tool("memory_recall", {"query": "busy_timeout"}))
    assert all(m["id"] != bid for m in rec3["memories"])
    rec4 = _payload(
        await server.call_tool(
            "memory_recall", {"query": "busy_timeout", "include_deprecated": True}
        )
    )
    assert any(m["id"] == bid for m in rec4["memories"])

    # read-only tools must NOT create namespaces for typos — they error instead
    bad = _payload(await server.call_tool("memory_search", {"namespace": "tyop"}))
    assert not bad["ok"] and "not found" in bad["error"]
    bad_stats = _payload(await server.call_tool("memory_stats", {"namespace": "tyop"}))
    assert not bad_stats["ok"]
    bad_recall = _payload(
        await server.call_tool("memory_recall", {"query": "wal", "namespace": "tyop"})
    )
    assert not bad_recall["ok"] and "not found" in bad_recall["error"]
    ns2 = _payload(await server.call_tool("memory_namespaces", {"action": "list"}))
    assert all(n["name"] != "tyop" for n in ns2["namespaces"])


@pytest.fixture
def limited_server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "limited.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "limited")
    monkeypatch.setenv("MEMORY_AUTO_CONTEXT_LIMIT", "1")
    from gingugu.server import build_server

    return build_server()


@pytest.mark.asyncio
async def test_context_limit_defaults_from_config(limited_server) -> None:
    # Regression: MEMORY_AUTO_CONTEXT_LIMIT was loaded into config but never
    # used — the handler hardcoded limit=10.
    for i in range(3):
        stored = _payload(
            await limited_server.call_tool(
                "memory_store",
                {"content": f"note number {i}", "title": f"note-{i}", "type": "fact"},
            )
        )
        assert stored["ok"]
    ctx = _payload(await limited_server.call_tool("memory_context", {}))
    assert ctx["ok"]
    assert ctx["count"] == 1  # config limit, not the old hardcoded 10
    # An explicit limit still overrides the config default.
    ctx2 = _payload(await limited_server.call_tool("memory_context", {"limit": 3}))
    assert ctx2["count"] == 3


@pytest.mark.asyncio
async def test_retrieval_handlers_credit_access(server) -> None:
    """recall, search, and context must each bump access_count and write
    access_log rows for the seeds they return — that's what powers the
    Memory Explorer's Access Activity chart."""
    a = _payload(
        await server.call_tool(
            "memory_store",
            {"content": "use WAL mode", "title": "wal", "type": "pattern"},
        )
    )
    b = _payload(
        await server.call_tool(
            "memory_store",
            {"content": "busy_timeout avoids SQLITE_BUSY", "title": "busy", "type": "pattern"},
        )
    )
    assert a["ok"] and b["ok"]

    # Baseline: no accesses yet.
    s0 = _payload(await server.call_tool("memory_stats", {}))
    assert s0["stats"]["access_log_rows"] == 0

    # recall credits its returned seeds.
    rec = _payload(await server.call_tool("memory_recall", {"query": "wal mode"}))
    assert rec["count"] >= 1
    s1 = _payload(await server.call_tool("memory_stats", {}))
    assert s1["stats"]["access_log_rows"] == rec["count"]

    # search adds another row per returned seed.
    srch = _payload(await server.call_tool("memory_search", {}))
    s2 = _payload(await server.call_tool("memory_stats", {}))
    assert s2["stats"]["access_log_rows"] == s1["stats"]["access_log_rows"] + srch["count"]

    # context adds more on top.
    ctx = _payload(await server.call_tool("memory_context", {}))
    s3 = _payload(await server.call_tool("memory_stats", {}))
    assert s3["stats"]["access_log_rows"] == s2["stats"]["access_log_rows"] + ctx["count"]

    # access_count on a returned memory is non-zero after the dust settles.
    final = _payload(await server.call_tool("memory_search", {"tags": ""}))
    assert any(m["access_count"] > 0 for m in final["memories"])


@pytest.mark.asyncio
async def test_recall_fresh_config_namespace_returns_empty(limited_server) -> None:
    # Recall before anything is stored: the config-resolved namespace doesn't
    # exist yet — return an empty ok result, and don't create the namespace.
    rec = _payload(await limited_server.call_tool("memory_recall", {"query": "anything"}))
    assert rec["ok"] and rec["count"] == 0
    ns = _payload(await limited_server.call_tool("memory_namespaces", {"action": "list"}))
    assert all(n["name"] != "limited" for n in ns["namespaces"])
