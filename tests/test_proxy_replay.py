"""A brain restart must not cost the client the calls it makes next.

A long-lived client (ChatGPT desktop) keeps one proxy across a brain restart.
The restarted brain has forgotten every derived token (401) and every MCP
session (404). Both refusals happen before a tool runs, so a call refused that
way is safe to send again - and the proxy does, exactly once. A call whose fate
is unknown is still failed, never replayed: a memory_store would write twice.
"""

from __future__ import annotations

import sqlite3

import anyio
import pytest
from mcp.shared.exceptions import McpError

from tests.proxy_fixtures import Served, free_port, make_brain, payload, through_proxy

BATCH = 5


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


def _stored(brain, prefix: str) -> list[str]:
    with sqlite3.connect(brain.tmp_path / "brain.db") as db:
        rows = db.execute("SELECT title FROM memories WHERE title LIKE ?", (f"{prefix}%",))
        return sorted(title for (title,) in rows)


async def _batch(session, prefix: str) -> list[dict]:
    """BATCH concurrent memory_store calls, as Tyrone sends them; one try each."""
    results: list[dict] = []

    async def store(i: int) -> None:
        args = {"title": f"{prefix}{i}", "content": f"cargo {i}", "type": "fact"}
        results.append(payload(await session.call_tool("memory_store", args)))

    with anyio.fail_after(20):
        async with anyio.create_task_group() as tg:
            for i in range(BATCH):
                tg.start_soon(store, i)
    return results


async def test_a_batch_survives_the_brain_forgetting_derived_tokens(brain):
    served = Served(free_port())
    await served.start(brain.app())
    try:
        async with through_proxy(served.url, brain.machine, home="gingugu") as (s, outcome):
            await s.initialize()
            assert payload(await s.call_tool("memory_stats", {}))["ok"]
            brain.derived._entries.clear()  # the token dies; the MCP session lives
            results = await _batch(s, "forgot-")
            assert all(r["ok"] for r in results), results
        assert "raised" not in outcome, outcome.get("raised")
        assert _stored(brain, "forgot-") == [f"forgot-{i}" for i in range(BATCH)]
    finally:
        await served.stop()


async def test_a_batch_right_after_a_brain_restart_lands_exactly_once(brain):
    # Tyrone, 2026-10-04: the Pi restarted under a live proxy and a batch of
    # memory_store calls all came back "remote brain unavailable, reconnecting".
    served = Served(free_port())
    await served.start(brain.app())
    try:
        async with through_proxy(served.url, brain.machine, home="gingugu") as (s, outcome):
            await s.initialize()
            assert payload(await s.call_tool("memory_stats", {}))["ok"]
            await served.stop()
            await served.start(brain.app(fresh_derived=True))
            results = await _batch(s, "restart-")
            assert all(r["ok"] for r in results), results
        assert "raised" not in outcome, outcome.get("raised")
        assert _stored(brain, "restart-") == [f"restart-{i}" for i in range(BATCH)]
    finally:
        await served.stop()


async def test_an_mcp_endpoint_refusing_every_token_does_not_mint_one_per_call(brain):
    # Security review: a brain (or a proxy in front of it) that 401s every fresh
    # token must not make each client call derive another - the brain caps live
    # derived tokens, and a full cap locks every machine out.
    inner = brain.app()
    state = {"refusing": False}

    async def refusing(scope, receive, send):
        if state["refusing"] and scope["type"] == "http" and scope["path"].startswith("/mcp"):
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        await inner(scope, receive, send)

    served = Served(free_port())
    await served.start(refusing)
    try:
        async with through_proxy(served.url, brain.machine, home="gingugu") as (s, outcome):
            await s.initialize()
            before = len([p for p, _ in brain.seen if p == "/token/derive"])
            state["refusing"] = True
            with anyio.move_on_after(4):
                while True:
                    with pytest.raises(McpError):
                        await s.call_tool("memory_stats", {})
                    await anyio.sleep(0.05)
            state["refusing"] = False
        derives = len([p for p, _ in brain.seen if p == "/token/derive"]) - before
        assert derives <= 3, f"{derives} tokens minted in 4s of refusals"
        assert "raised" not in outcome, outcome.get("raised")
    finally:
        await served.stop()


async def test_a_refused_call_is_not_replayed_after_a_long_outage(brain, monkeypatch):
    # Security review: the client may have given up and re-sent the call itself
    # by then, so a late replay would write twice. A stale one gets an error.
    from gingugu import proxy

    monkeypatch.setattr(proxy, "_REPLAY_MAX_AGE", 0.5)
    inner = brain.app()
    state = {"gone": False}

    async def forgetful(scope, receive, send):
        # /mcp answers 404 (session unknown) while /token/derive keeps working.
        if state["gone"] and scope["type"] == "http" and scope["path"].startswith("/mcp"):
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        await inner(scope, receive, send)

    served = Served(free_port())
    await served.start(forgetful)
    try:
        async with through_proxy(served.url, brain.machine, home="gingugu") as (s, outcome):
            await s.initialize()
            assert payload(await s.call_tool("memory_stats", {}))["ok"]
            state["gone"] = True

            async def heal() -> None:
                await anyio.sleep(2)  # well past _REPLAY_MAX_AGE
                state["gone"] = False

            async with anyio.create_task_group() as tg:
                tg.start_soon(heal)
                with anyio.fail_after(15):
                    with pytest.raises(McpError):
                        args = {"title": "stale-call", "content": "cargo", "type": "fact"}
                        await s.call_tool("memory_store", args)
        assert "raised" not in outcome, outcome.get("raised")
        assert _stored(brain, "stale-") == []
    finally:
        await served.stop()


async def test_a_call_made_while_the_proxy_reconnects_waits_for_the_link(brain):
    served = Served(free_port())
    await served.start(brain.app())
    try:
        async with through_proxy(served.url, brain.machine, home="gingugu") as (s, outcome):
            await s.initialize()
            assert payload(await s.call_tool("memory_stats", {}))["ok"]
            await served.stop()
            await served.start(brain.app(fresh_derived=True))
            results: list[dict] = []

            async def store(title: str) -> None:
                args = {"title": title, "content": "cargo", "type": "fact"}
                results.append(payload(await s.call_tool("memory_store", args)))

            with anyio.fail_after(20):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(store, "held-first")  # its 404 drops the link
                    await anyio.sleep(0.1)  # inside the reconnect backoff
                    tg.start_soon(store, "held-second")
            assert len(results) == 2 and all(r["ok"] for r in results), results
        assert "raised" not in outcome, outcome.get("raised")
        assert _stored(brain, "held-") == ["held-first", "held-second"]
    finally:
        await served.stop()
