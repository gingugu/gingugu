"""The proxy outlives the brain: token refresh, outages, restarts.

A client such as ChatGPT desktop keeps one `gingugu` process for days, so the
proxy refreshes its derived token before expiry and, when the brain goes away,
errors calls within a bounded wait (never a hang) while it reconnects in the
background. Once back, it replays the client's initialize itself: the client
never re-handshakes. A call in flight at the moment of loss is failed, never
replayed - a memory_store sent twice would write twice. (Calls the brain
refused unrun are a different case: see test_proxy_replay.)
"""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.exceptions import McpError

from tests.proxy_fixtures import Served, free_port, make_brain, payload, through_proxy


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch, credentials=False)


async def _until_ok(session, tool: str, args: dict, within: float) -> dict:
    """Retry ``tool`` until it answers; McpError while the brain is away."""
    with anyio.fail_after(within):
        while True:
            try:
                return payload(await session.call_tool(tool, args))
            except McpError:
                await anyio.sleep(0.2)


async def test_token_is_refreshed_before_it_expires(brain):
    served = Served(free_port())
    await served.start(brain.app())
    try:
        async with through_proxy(served.url, brain.machine, home="gingugu", ttl=2) as (s, _):
            await s.initialize()
            await anyio.sleep(3.2)  # the first token is dead by now
            # Answered first try: a refreshed token, not a 401 and a reconnect.
            assert payload(await s.call_tool("memory_stats", {}))["ok"]
    finally:
        await served.stop()


@pytest.mark.parametrize("restart", ["fresh_derived", "same_derived"])
async def test_survives_a_brain_restart_without_a_new_handshake(brain, restart):
    # fresh_derived: a real restart, every derived token gone (401 path).
    # same_derived: the token is still valid but the MCP session is not (404 path).
    served = Served(free_port())
    await served.start(brain.app())
    try:
        async with through_proxy(served.url, brain.machine, home="gingugu") as (s, outcome):
            await s.initialize()
            stored = payload(
                await s.call_tool(
                    "memory_store", {"title": "before", "content": "lantern", "type": "fact"}
                )
            )
            assert stored["ok"]
            await served.stop()
            with anyio.fail_after(10):  # down: a fast error, never a hang
                with pytest.raises(McpError):
                    await s.call_tool("memory_stats", {})
            await served.start(brain.app(fresh_derived=restart == "fresh_derived"))
            recalled = await _until_ok(s, "memory_recall", {"query": "lantern"}, within=20)
            assert recalled["ok"] and "before" in str(recalled)
        assert "raised" not in outcome, outcome.get("raised")
    finally:
        await served.stop()


async def test_owner_token_revoked_while_down_ends_the_proxy_loudly(brain):
    from gingugu import proxy
    from gingugu.serve_tokens import TokenStore

    served = Served(free_port())
    await served.start(brain.app())
    try:
        async with through_proxy(served.url, brain.machine, home="gingugu") as (s, outcome):
            await s.initialize()
            await served.stop()
            TokenStore(brain.tmp_path / "serve_tokens.json").revoke("macbookpro")
            await served.start(brain.app(fresh_derived=True))
            with anyio.fail_after(20):
                while "raised" not in outcome:
                    try:
                        await s.call_tool("memory_stats", {})
                    except McpError:
                        pass
                    await anyio.sleep(0.2)
        assert isinstance(outcome["raised"], proxy.ProxyLost)
        assert "gingugu remote login" in str(outcome["raised"])
        assert brain.machine not in str(outcome["raised"])
    finally:
        await served.stop()
