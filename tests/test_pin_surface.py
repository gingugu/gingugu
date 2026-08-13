"""Pinned tier over the live MCP tool surface, including the budget guard."""

from __future__ import annotations

import json

import pytest

from gingugu.context import PINNED_HARD_CAP


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "pins.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "pins")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _store(server, title: str) -> str:
    res = _payload(
        await server.call_tool(
            "memory_store",
            {"title": title, "content": f"body of {title}", "type": "preference"},
        )
    )
    assert res["ok"]
    return res["memory"]["id"]


@pytest.mark.asyncio
async def test_pin_and_unpin_round_trip(server) -> None:
    mid = await _store(server, "a hard rule")

    pinned = _payload(await server.call_tool("memory_update", {"memory_id": mid, "pinned": True}))
    assert pinned["ok"]
    assert pinned["memory"]["pinned"] is True

    unpinned = _payload(
        await server.call_tool("memory_update", {"memory_id": mid, "pinned": False})
    )
    assert unpinned["ok"]
    # Absent rather than false: ordinary memories don't carry the key at all.
    assert "pinned" not in unpinned["memory"]


@pytest.mark.asyncio
async def test_pin_surfaces_in_context(server) -> None:
    mid = await _store(server, "never deploy on friday")
    await server.call_tool("memory_update", {"memory_id": mid, "pinned": True})
    for i in range(8):
        await _store(server, f"unrelated note {i}")

    ctx = _payload(
        await server.call_tool("memory_context", {"task_hint": "unrelated note", "limit": 3})
    )
    assert ctx["ok"]
    assert mid in {m["id"] for m in ctx["memories"]}


@pytest.mark.asyncio
async def test_pin_budget_is_enforced(server) -> None:
    """The cap is what keeps the tier meaningful, so exceeding it must fail loudly."""
    for i in range(PINNED_HARD_CAP):
        mid = await _store(server, f"rule {i}")
        res = _payload(await server.call_tool("memory_update", {"memory_id": mid, "pinned": True}))
        assert res["ok"], f"pin {i} should fit within the cap"

    overflow = await _store(server, "one rule too many")
    refused = _payload(
        await server.call_tool("memory_update", {"memory_id": overflow, "pinned": True})
    )
    assert refused["ok"] is False
    assert "pin limit reached" in refused["error"]


@pytest.mark.asyncio
async def test_repinning_an_existing_pin_is_idempotent_at_the_cap(server) -> None:
    """A full tier must not make its own members unwritable.

    Re-pinning something already pinned consumes no new budget, so it has to
    succeed even when the namespace is at the cap — otherwise a routine
    no-op update starts failing once the tier fills.
    """
    ids = []
    for i in range(PINNED_HARD_CAP):
        mid = await _store(server, f"rule {i}")
        await server.call_tool("memory_update", {"memory_id": mid, "pinned": True})
        ids.append(mid)

    again = _payload(await server.call_tool("memory_update", {"memory_id": ids[0], "pinned": True}))
    assert again["ok"] is True


@pytest.mark.asyncio
async def test_unpinning_frees_budget(server) -> None:
    ids = []
    for i in range(PINNED_HARD_CAP):
        mid = await _store(server, f"rule {i}")
        await server.call_tool("memory_update", {"memory_id": mid, "pinned": True})
        ids.append(mid)

    await server.call_tool("memory_update", {"memory_id": ids[0], "pinned": False})

    newcomer = await _store(server, "a better rule")
    res = _payload(await server.call_tool("memory_update", {"memory_id": newcomer, "pinned": True}))
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_pin_on_missing_memory_errors(server) -> None:
    res = _payload(
        await server.call_tool("memory_update", {"memory_id": "does-not-exist", "pinned": True})
    )
    assert res["ok"] is False
    assert "not found" in res["error"]
