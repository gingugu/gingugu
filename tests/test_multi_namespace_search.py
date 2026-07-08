"""Tests for multi-namespace ``memory_recall``/``memory_search`` (CSV namespace
lists) and the single-namespace guardrails: the ``memory_store`` junk-namespace
guard and the comma-hint on tools that take exactly one namespace.
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
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "multi.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "proj-x")
    from gingugu.server import build_server

    return build_server()


async def _store(server, *, namespace: str, title: str, content: str) -> str:
    out = _payload(
        await server.call_tool(
            "memory_store",
            {"content": content, "title": title, "type": "fact", "namespace": namespace},
        )
    )
    assert out["ok"]
    return out["memory"]["id"]


async def _seed(server) -> dict[str, str]:
    """One argocd memory in each of ns-a/ns-b, one unrelated in proj-x."""
    return {
        "a": await _store(
            server,
            namespace="ns-a",
            title="argocd sync quirk",
            content="argocd sync fails when the appset overlaps",
        ),
        "b": await _store(
            server,
            namespace="ns-b",
            title="argocd rollout order",
            content="argocd rollout must finish before the deploy freeze",
        ),
        "x": await _store(
            server,
            namespace="proj-x",
            title="unrelated lore",
            content="the kraken sleeps under the dock",
        ),
    }


@pytest.mark.asyncio
async def test_recall_csv_searches_both_namespaces(server) -> None:
    ids = await _seed(server)
    out = _payload(
        await server.call_tool("memory_recall", {"query": "argocd", "namespace": "ns-a,ns-b"})
    )
    assert out["ok"]
    assert out["namespaces"] == ["ns-a", "ns-b"]
    assert "namespace" not in out
    got = {m["id"] for m in out["memories"]}
    assert ids["a"] in got and ids["b"] in got
    assert ids["x"] not in got
    by_id = {m["id"]: m for m in out["memories"]}
    assert by_id[ids["a"]]["namespace"] == "ns-a"
    assert by_id[ids["b"]]["namespace"] == "ns-b"


@pytest.mark.asyncio
async def test_recall_limit_is_total_across_namespaces(server) -> None:
    """Unlike memory_context (limit per namespace), recall's limit caps the
    merged ranked list."""
    for i in range(3):
        await _store(server, namespace="ns-a", title=f"argocd note a{i}", content=f"argocd a{i}")
        await _store(server, namespace="ns-b", title=f"argocd note b{i}", content=f"argocd b{i}")
    out = _payload(
        await server.call_tool(
            "memory_recall", {"query": "argocd", "namespace": "ns-a,ns-b", "limit": 4}
        )
    )
    assert out["ok"]
    assert out["count"] == 4


@pytest.mark.asyncio
async def test_recall_single_namespace_shape_unchanged(server) -> None:
    await _seed(server)
    out = _payload(
        await server.call_tool("memory_recall", {"query": "argocd", "namespace": "ns-a"})
    )
    assert out["ok"]
    assert out["namespace"] == "ns-a"
    assert "namespaces" not in out
    assert all(m["namespace"] == "ns-a" for m in out["memories"])


@pytest.mark.asyncio
async def test_recall_unknown_member_errors_without_minting(server) -> None:
    """The exact failure seen in the wild: a CSV list where one namespace
    doesn't exist must name the missing one and never create it."""
    await _seed(server)
    out = _payload(
        await server.call_tool("memory_recall", {"query": "argocd", "namespace": "ns-a,ghost"})
    )
    assert not out["ok"]
    assert "namespace 'ghost' not found" in out["error"]

    out = _payload(
        await server.call_tool("memory_recall", {"query": "argocd", "namespace": "ghost,wraith"})
    )
    assert not out["ok"]
    assert "namespaces 'ghost', 'wraith' not found" in out["error"]

    listing = _payload(await server.call_tool("memory_namespaces", {"action": "list"}))
    names = [ns["name"] for ns in listing["namespaces"]]
    assert "ghost" not in names and "wraith" not in names


@pytest.mark.asyncio
async def test_search_csv_scopes_and_stamps(server) -> None:
    ids = await _seed(server)
    out = _payload(
        await server.call_tool("memory_search", {"namespace": "ns-a,ns-b", "sort_by": "created"})
    )
    assert out["ok"]
    assert out["namespaces"] == ["ns-a", "ns-b"]
    assert {m["id"] for m in out["memories"]} == {ids["a"], ids["b"]}
    assert all(m["namespace"] in ("ns-a", "ns-b") for m in out["memories"])


@pytest.mark.asyncio
async def test_search_global_shape_unchanged(server) -> None:
    await _seed(server)
    out = _payload(await server.call_tool("memory_search", {}))
    assert out["ok"]
    assert "namespaces" not in out
    # every read surface stamps a readable per-memory namespace
    assert all("namespace" in m for m in out["memories"])


@pytest.mark.asyncio
async def test_store_rejects_comma_namespace(server) -> None:
    """memory_store must fail fast instead of minting a namespace literally
    named "a,b" and silently storing into it."""
    out = _payload(
        await server.call_tool(
            "memory_store",
            {"content": "junk", "title": "junk", "type": "fact", "namespace": "ns-a,ns-b"},
        )
    )
    assert not out["ok"]
    assert "single namespace" in out["error"]

    listing = _payload(await server.call_tool("memory_namespaces", {"action": "list"}))
    assert "ns-a,ns-b" not in [ns["name"] for ns in listing["namespaces"]]


@pytest.mark.asyncio
async def test_stats_comma_namespace_gets_hint(server) -> None:
    out = _payload(await server.call_tool("memory_stats", {"namespace": "crow,gingugu"}))
    assert not out["ok"]
    assert "not found" in out["error"]
    assert "comma-separated lists are supported by" in out["error"]


@pytest.mark.asyncio
async def test_export_comma_namespace_gets_hint(server) -> None:
    out = _payload(await server.call_tool("memory_export", {"namespace": "crow,gingugu"}))
    assert not out["ok"]
    assert "comma-separated lists are supported by" in out["error"]
