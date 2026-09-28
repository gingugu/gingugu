"""Tests for namespace auto-widen on the lookup read surfaces.

Two rules. An unconfigured server (no MEMORY_NAMESPACE, no
MEMORY_NAMESPACE_PATH) has no "current project", so ``memory_recall`` with no
namespace searches every namespace instead of the ``default`` fallback. And a
scoped lookup that comes back empty reruns once across every namespace,
reporting ``widened_from`` so the caller knows the answer came from elsewhere.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    """Unwrap a FastMCP tool result into its JSON dict."""
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def _build(tmp_path, monkeypatch, namespace: str | None):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "widen.db"))
    monkeypatch.delenv("MEMORY_NAMESPACE_PATH", raising=False)
    if namespace is None:
        monkeypatch.delenv("MEMORY_NAMESPACE", raising=False)
    else:
        monkeypatch.setenv("MEMORY_NAMESPACE", namespace)
    from gingugu.server import build_server

    return build_server()


@pytest.fixture
def unconfigured(tmp_path, monkeypatch):
    return _build(tmp_path, monkeypatch, None)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    return _build(tmp_path, monkeypatch, "proj-x")


async def _call(server, tool: str, args: dict) -> dict:
    out = _payload(await server.call_tool(tool, args))
    assert out["ok"], out
    return out


async def _seed(server) -> dict[str, str]:
    """An argocd memory in ns-a only; unrelated lore in ns-b and proj-x."""
    ids = {}
    for key, ns, title, content in [
        ("a", "ns-a", "argocd sync quirk", "argocd sync fails when the appset overlaps"),
        ("b", "ns-b", "tide tables", "the tide turns at the third bell"),
        ("x", "proj-x", "kraken lore", "the kraken sleeps under the dock"),
    ]:
        out = await _call(
            server,
            "memory_store",
            {"content": content, "title": title, "type": "fact", "namespace": ns},
        )
        ids[key] = out["memory"]["id"]
    return ids


# --- unconfigured server: an omitted namespace means every namespace ---------


@pytest.mark.asyncio
async def test_unconfigured_recall_searches_every_namespace(unconfigured) -> None:
    ids = await _seed(unconfigured)
    out = await _call(unconfigured, "memory_recall", {"query": "argocd"})
    assert [m["id"] for m in out["memories"]] == [ids["a"]]
    assert out["memories"][0]["namespace"] == "ns-a"
    assert out["scope"] == "all"
    assert "namespace" not in out
    assert "widened_from" not in out


@pytest.mark.asyncio
async def test_unconfigured_recall_never_mints_default(unconfigured) -> None:
    await _seed(unconfigured)
    await _call(unconfigured, "memory_recall", {"query": "nothing matches this"})
    names = {n["name"] for n in (await _call(unconfigured, "memory_namespaces", {}))["namespaces"]}
    assert "default" not in names


# --- configured / explicit scope: widen only on an empty result -------------


@pytest.mark.asyncio
async def test_configured_recall_with_hits_stays_scoped(configured) -> None:
    ids = await _seed(configured)
    out = await _call(configured, "memory_recall", {"query": "kraken"})
    assert [m["id"] for m in out["memories"]] == [ids["x"]]
    assert out["namespace"] == "proj-x"
    assert "widened_from" not in out
    assert "scope" not in out


@pytest.mark.asyncio
async def test_configured_recall_widens_on_empty(configured) -> None:
    ids = await _seed(configured)
    out = await _call(configured, "memory_recall", {"query": "argocd"})
    assert [m["id"] for m in out["memories"]] == [ids["a"]]
    assert out["memories"][0]["namespace"] == "ns-a"
    assert out["widened_from"] == ["proj-x"]
    assert out["scope"] == "all"
    assert "namespace" not in out


@pytest.mark.asyncio
async def test_configured_recall_widens_when_namespace_not_created_yet(
    tmp_path, monkeypatch
) -> None:
    server = _build(tmp_path, monkeypatch, "brand-new")
    ids = await _seed(server)
    out = await _call(server, "memory_recall", {"query": "argocd"})
    assert [m["id"] for m in out["memories"]] == [ids["a"]]
    assert out["widened_from"] == ["brand-new"]


@pytest.mark.asyncio
async def test_explicit_recall_widens_on_empty(configured) -> None:
    ids = await _seed(configured)
    out = await _call(configured, "memory_recall", {"query": "argocd", "namespace": "ns-b"})
    assert [m["id"] for m in out["memories"]] == [ids["a"]]
    assert out["widened_from"] == ["ns-b"]


@pytest.mark.asyncio
async def test_explicit_multi_recall_widens_on_empty(configured) -> None:
    ids = await _seed(configured)
    out = await _call(configured, "memory_recall", {"query": "argocd", "namespace": "ns-b,proj-x"})
    assert [m["id"] for m in out["memories"]] == [ids["a"]]
    assert out["widened_from"] == ["ns-b", "proj-x"]
    assert "namespaces" not in out


@pytest.mark.asyncio
async def test_widen_that_still_finds_nothing_says_so(configured) -> None:
    await _seed(configured)
    out = await _call(configured, "memory_recall", {"query": "zanzibar"})
    assert out["count"] == 0
    assert out["widened_from"] == ["proj-x"]
    assert out["scope"] == "all"


@pytest.mark.asyncio
async def test_widen_keeps_every_filter(configured) -> None:
    await _seed(configured)
    out = await _call(configured, "memory_recall", {"query": "argocd", "type": "decision"})
    assert out["count"] == 0
    assert out["widened_from"] == ["proj-x"]


@pytest.mark.asyncio
async def test_unknown_explicit_namespace_is_still_an_error(configured) -> None:
    await _seed(configured)
    out = _payload(
        await configured.call_tool("memory_recall", {"query": "argocd", "namespace": "nope"})
    )
    assert out["ok"] is False
    assert "nope" in out["error"]


# --- memory_search: widen lookups, never filter-only sweeps -----------------


@pytest.mark.asyncio
async def test_search_query_widens_on_empty(configured) -> None:
    out, seeded = await _call_seed_and_search(configured, {"query": "argocd", "namespace": "ns-b"})
    assert [m["id"] for m in out["memories"]] == [seeded["a"]]
    assert out["widened_from"] == ["ns-b"]
    assert out["scope"] == "all"


@pytest.mark.asyncio
async def test_search_query_with_hits_stays_scoped(configured) -> None:
    out, seeded = await _call_seed_and_search(configured, {"query": "tide", "namespace": "ns-b"})
    assert [m["id"] for m in out["memories"]] == [seeded["b"]]
    assert "widened_from" not in out


@pytest.mark.asyncio
async def test_search_filter_only_sweep_never_widens(configured) -> None:
    out, _ = await _call_seed_and_search(configured, {"namespace": "ns-b", "type": "decision"})
    assert out["count"] == 0
    assert "widened_from" not in out


async def _call_seed_and_search(server, args: dict) -> tuple[dict, dict[str, str]]:
    seeded = await _seed(server)
    return await _call(server, "memory_search", args), seeded
