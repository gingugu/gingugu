"""Tests for ``compact`` mode on ``memory_recall`` and ``memory_search``.

Compact reads exist to keep large recalls under MCP clients' tool-result
token budgets: title + ~200-char excerpt instead of full content, with
``include_related`` extras compacted too (they share the same payload).
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
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "compact.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "proj-x")
    # Deterministic BM25-only retrieval: these tests assert the
    # via_relation plumbing, which requires the neighbour NOT to
    # surface as a seed via the semantic pool.
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


LONG = "argocd rollout detail " * 30  # well past the ~200-char excerpt cap


async def _store(server, *, title: str, namespace: str = "proj-x", content: str = LONG) -> str:
    out = _payload(
        await server.call_tool(
            "memory_store",
            {"content": content, "title": title, "type": "fact", "namespace": namespace},
        )
    )
    assert out["ok"]
    return out["memory"]["id"]


def _assert_compact_shape(mem: dict) -> None:
    assert "content" not in mem
    assert "summary" in mem and len(mem["summary"]) <= 210
    assert mem["id"] and mem["title"]
    assert "namespace" in mem  # readable stamp survives compact mode
    assert "created_at" not in mem and "access_count" not in mem


@pytest.mark.asyncio
async def test_recall_compact_returns_excerpt_not_content(server) -> None:
    await _store(server, title="argocd sync one")
    await _store(server, title="argocd sync two")

    out = _payload(await server.call_tool("memory_recall", {"query": "argocd", "compact": True}))
    assert out["ok"] and out["count"] >= 2
    for mem in out["memories"]:
        _assert_compact_shape(mem)


@pytest.mark.asyncio
async def test_recall_compact_still_counts_as_real_access(server) -> None:
    """Compact changes the payload, not the semantics: a recall is real usage
    signal and must still credit access."""
    await _store(server, title="argocd sync one")

    out = _payload(await server.call_tool("memory_recall", {"query": "argocd", "compact": True}))
    assert out["count"] >= 1

    stats = _payload(await server.call_tool("memory_stats", {}))
    assert stats["stats"]["access_log_rows"] == out["count"]


@pytest.mark.asyncio
async def test_recall_compact_include_related_extras_are_compact(server) -> None:
    seed = await _store(server, title="argocd sync quirk")
    # Content must NOT match the query, or the neighbour becomes a seed
    # instead of a via_relation extra.
    neighbour = await _store(
        server, title="unrelated kraken lore", content="the kraken sleeps under the dock " * 12
    )
    linked = _payload(
        await server.call_tool(
            "memory_relate",
            {"source_id": seed, "target_id": neighbour, "relation_type": "related_to"},
        )
    )
    assert linked["ok"]

    out = _payload(
        await server.call_tool(
            "memory_recall",
            {"query": "argocd sync quirk", "compact": True, "include_related": True},
        )
    )
    assert out["ok"]
    extras = [m for m in out["memories"] if m.get("via_relation")]
    assert extras, "related neighbour was not surfaced"
    for mem in extras:
        _assert_compact_shape(mem)


@pytest.mark.asyncio
async def test_recall_compact_multi_namespace_stamps(server) -> None:
    await _store(server, title="argocd note a", namespace="ns-a")
    await _store(server, title="argocd note b", namespace="ns-b")

    out = _payload(
        await server.call_tool(
            "memory_recall", {"query": "argocd", "namespace": "ns-a,ns-b", "compact": True}
        )
    )
    assert out["ok"] and out["namespaces"] == ["ns-a", "ns-b"]
    assert {m["namespace"] for m in out["memories"]} == {"ns-a", "ns-b"}
    for mem in out["memories"]:
        _assert_compact_shape(mem)


@pytest.mark.asyncio
async def test_search_compact_returns_excerpt_not_content(server) -> None:
    await _store(server, title="argocd sync one")

    out = _payload(await server.call_tool("memory_search", {"compact": True}))
    assert out["ok"] and out["count"] >= 1
    for mem in out["memories"]:
        _assert_compact_shape(mem)


@pytest.mark.asyncio
async def test_full_mode_shape_unchanged_by_default(server) -> None:
    """compact defaults off: full content and bookkeeping fields still present."""
    await _store(server, title="argocd sync one")

    for tool, params in (
        ("memory_recall", {"query": "argocd"}),
        ("memory_search", {}),
    ):
        out = _payload(await server.call_tool(tool, params))
        assert out["ok"] and out["count"] >= 1
        for mem in out["memories"]:
            assert "content" in mem and "summary" not in mem
            assert "created_at" in mem and "access_count" in mem
