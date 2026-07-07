"""Tests for the context-efficiency surface of ``memory_context``:
multi-namespace loading with cross-namespace dedupe, compact mode, and the
protocol-read access semantics (no access_count inflation).
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    """Unwrap a FastMCP tool result into its JSON dict."""
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "ctx.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "proj-x")
    from gingugu.server import build_server

    return build_server()


async def _seed(server) -> tuple[str, str]:
    """One verified global pattern in crow-x, one project fact in proj-x."""
    pattern = _payload(
        await server.call_tool(
            "memory_store",
            {
                "content": "always verify an image tag exists before committing it",
                "title": "verify tags",
                "type": "pattern",
                "confidence": "verified",
                "namespace": "crow-x",
            },
        )
    )
    fact = _payload(
        await server.call_tool(
            "memory_store",
            {
                "content": "the project deploys via ArgoCD appsets",
                "title": "deploy path",
                "type": "fact",
                "namespace": "proj-x",
            },
        )
    )
    assert pattern["ok"] and fact["ok"]
    return pattern["memory"]["id"], fact["memory"]["id"]


@pytest.mark.asyncio
async def test_multi_namespace_dedupes_cross_namespace_bleed(server) -> None:
    """Loading two namespaces in one call must return a memory that surfaces
    in both (via the cross-namespace pattern bucket + its home recency bucket)
    exactly once, stamped with its home namespace."""
    pattern_id, fact_id = await _seed(server)

    ctx = _payload(await server.call_tool("memory_context", {"namespace": "proj-x,crow-x"}))
    assert ctx["ok"]
    ids = [m["id"] for m in ctx["memories"]]
    assert ids.count(pattern_id) == 1
    assert fact_id in ids
    assert ctx["namespaces"] == ["proj-x", "crow-x"]
    assert ctx["duplicates_removed"] >= 1

    by_id = {m["id"]: m for m in ctx["memories"]}
    assert by_id[pattern_id]["namespace"] == "crow-x"
    assert by_id[fact_id]["namespace"] == "proj-x"


@pytest.mark.asyncio
async def test_namespace_list_is_deduped_and_stripped(server) -> None:
    await _seed(server)
    ctx = _payload(
        await server.call_tool("memory_context", {"namespace": " proj-x , crow-x ,proj-x"})
    )
    assert ctx["ok"]
    assert ctx["namespaces"] == ["proj-x", "crow-x"]


@pytest.mark.asyncio
async def test_blank_csv_items_do_not_mint_empty_namespace(server) -> None:
    """Peer-review regression: 'proj-x,' must not get_or_create a namespace
    literally named "" — blank items are dropped before any bootstrap."""
    await _seed(server)
    ctx = _payload(await server.call_tool("memory_context", {"namespace": "proj-x, ,"}))
    assert ctx["ok"]
    assert ctx["namespace"] == "proj-x"  # single-ns shape: blanks were dropped
    assert "namespaces" not in ctx

    listing = _payload(await server.call_tool("memory_namespaces", {"action": "list"}))
    assert all(ns["name"] for ns in listing["namespaces"]), "empty-named namespace created"


@pytest.mark.asyncio
async def test_single_namespace_shape_is_backward_compatible(server) -> None:
    """A single-namespace call keeps the historical ``namespace`` key and
    gains a readable per-memory namespace stamp."""
    await _seed(server)
    ctx = _payload(await server.call_tool("memory_context", {}))
    assert ctx["ok"]
    assert ctx["namespace"] == "proj-x"
    assert "namespaces" not in ctx
    assert all("namespace" in m for m in ctx["memories"])


@pytest.mark.asyncio
async def test_compact_mode_returns_excerpt_not_content(server) -> None:
    long_content = "gingugu " * 60  # well past the excerpt cap
    stored = _payload(
        await server.call_tool(
            "memory_store",
            {"content": long_content, "title": "long one", "type": "context"},
        )
    )
    assert stored["ok"]

    ctx = _payload(await server.call_tool("memory_context", {"compact": True}))
    assert ctx["ok"] and ctx["count"] >= 1
    for mem in ctx["memories"]:
        assert "content" not in mem
        assert "summary" in mem
        assert len(mem["summary"]) <= 210
        assert mem["title"]
        assert mem["namespace"] == "proj-x"


@pytest.mark.asyncio
async def test_context_load_does_not_count_as_access(server) -> None:
    """memory_context must not bump access_count or write access_log rows —
    protocol-driven session-start loads are not real usage signal."""
    await _seed(server)

    ctx = _payload(await server.call_tool("memory_context", {"namespace": "proj-x,crow-x"}))
    assert ctx["count"] >= 1

    stats = _payload(await server.call_tool("memory_stats", {}))
    assert stats["stats"]["access_log_rows"] == 0

    # memory_search reads rows before crediting its own access, so the
    # returned counts reflect the post-context state: still zero.
    found = _payload(await server.call_tool("memory_search", {"namespace": "proj-x"}))
    assert all(m["access_count"] == 0 for m in found["memories"])
