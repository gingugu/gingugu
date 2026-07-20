"""Tool-surface tests for the review-sweep workflow:
``memory_stats(review_limit=…)`` enumerates flagged memories,
``memory_search(ids=…)`` pulls their full bodies by exact ID.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "sweep.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "sweep-ns")
    from gingugu.server import build_server

    return build_server()


async def _store(server, **kwargs) -> str:
    out = _payload(await server.call_tool("memory_store", kwargs))
    assert out["ok"]
    return out["memory"]["id"]


@pytest.mark.asyncio
async def test_search_by_ids_fetches_exact_memories(server) -> None:
    a = await _store(server, content="alpha body", title="alpha", type="fact")
    b = await _store(server, content="beta body", title="beta", type="fact")

    out = _payload(await server.call_tool("memory_search", {"ids": f"{b},nope,{a}"}))
    assert out["ok"]
    assert [m["id"] for m in out["memories"]] == [b, a]
    assert out["missing"] == ["nope"]
    # Explicit reads credit access like every other deliberate retrieval.
    assert out["memories"][0]["content"] == "beta body"


@pytest.mark.asyncio
async def test_search_by_ids_includes_deprecated(server) -> None:
    dep = await _store(
        server,
        content="superseded state",
        title="old status",
        type="fact",
        confidence="deprecated",
    )
    out = _payload(await server.call_tool("memory_search", {"ids": dep}))
    assert [m["id"] for m in out["memories"]] == [dep]
    assert "missing" not in out


@pytest.mark.asyncio
async def test_stats_review_limit_uncaps_sample(server) -> None:
    """expired-date is ungated, so freshly stored memories flag immediately —
    seven flagged memories overflow the default sample of 5 but not a raised one."""
    for i in range(7):
        await _store(
            server,
            content=f"key {i} expires 2026-06-29, rotate it",
            title=f"expiry {i}",
            type="fact",
        )

    default = _payload(await server.call_tool("memory_stats", {}))["stats"]["review"]
    assert default["review_suggested"] == 7
    assert len(default["sample"]) == 5

    raised = _payload(await server.call_tool("memory_stats", {"review_limit": 50}))
    assert len(raised["stats"]["review"]["sample"]) == 7
