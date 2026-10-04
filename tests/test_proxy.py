"""`gingugu` in remote mode: a stdio <-> streamable-HTTP relay to the brain.

Every test drives a real ClientSession through ``proxy.run`` against the real
`gingugu serve` app on a loopback socket. The proxy is handed the machine's
owner token, trades it for a derived session token carrying the client's grant
and home namespace, and never puts the owner token on an MCP request.
"""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.exceptions import McpError

from tests.proxy_fixtures import make_brain, payload, serving, through_proxy

_CRED_TOOLS = {"credential_store", "credential_get", "credential_list", "credential_delete"}


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch)  # credential tools ON server-side


# --- the relay ---------------------------------------------------------------


async def test_round_trip_store_then_recall_lands_in_the_clients_home(brain):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="gingugu") as (session, _):
            init = await session.initialize()
            assert init.serverInfo.name == "gingugu"
            stored = payload(
                await session.call_tool(
                    "memory_store",
                    {"title": "proxied note", "content": "kraken tentacle almanac", "type": "fact"},
                )
            )
            recalled = payload(
                await session.call_tool("memory_recall", {"query": "kraken tentacle almanac"})
            )
    assert stored["ok"] and stored["namespace"] == "gingugu"  # not "server-default"
    assert recalled["ok"] and "proxied note" in str(recalled)
    assert "widened_from" not in recalled


async def test_the_owner_token_never_rides_an_mcp_request(brain):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine, home="gingugu") as (session, _):
            await session.initialize()
            await session.call_tool("memory_stats", {})
    sent = brain.mcp_auth()
    assert sent, "no MCP request was recorded"
    assert all(brain.machine not in auth for auth in sent)
    assert all(auth.startswith("Bearer ") for auth in sent)


async def test_tool_surface_is_the_servers_minus_credentials(brain):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine) as (session, _):
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
    assert "memory_store" in names and "memory_recall" in names
    assert not (names & _CRED_TOOLS)


@pytest.mark.parametrize("tool", sorted(_CRED_TOOLS))
async def test_credential_calls_are_refused_by_the_proxy(brain, tool):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine) as (session, _):
            await session.initialize()
            with pytest.raises(McpError) as err:
                await session.call_tool(tool, {"service_name": "x"})
            # The proxy's own refusal, not the server's: it names remote mode.
            assert "remote" in str(err.value).lower()
            # The session survives a refusal.
            assert payload(await session.call_tool("memory_stats", {}))["ok"]


# --- personas ----------------------------------------------------------------


async def test_a_persona_is_fenced_by_the_server(brain):
    async with serving(brain.app()) as served:
        async with through_proxy(served.url, brain.machine) as (owner, _):
            await owner.initialize()
            seeded = await owner.call_tool(
                "memory_store",
                {"title": "rule", "content": "owl", "type": "fact", "namespace": "crow"},
            )
            assert payload(seeded)["ok"]
        persona = {"grant": "tyrone=write,crow=read", "home": "tyrone"}
        async with through_proxy(served.url, brain.machine, **persona) as (session, _):
            await session.initialize()
            mine = payload(
                await session.call_tool(
                    "memory_store", {"title": "t", "content": "kraken", "type": "fact"}
                )
            )
            theirs = payload(
                await session.call_tool(
                    "memory_store",
                    {"title": "t", "content": "x", "type": "fact", "namespace": "crow"},
                )
            )
            read = payload(
                await session.call_tool("memory_recall", {"query": "owl", "namespace": "crow"})
            )
    assert mine["ok"] and mine["namespace"] == "tyrone"
    assert theirs["ok"] is False
    assert read["ok"] and read["count"] >= 1


# --- failure -----------------------------------------------------------------


async def test_a_rejected_owner_token_fails_loudly_not_a_hang(brain):
    from gingugu import proxy

    async with serving(brain.app()) as served:
        with anyio.fail_after(15):
            async with through_proxy(served.url, "not-the-token") as (session, outcome):
                with pytest.raises(McpError):
                    await session.initialize()
    raised = outcome.get("raised")
    assert isinstance(raised, proxy.ProxyLost), outcome
    assert "401" in str(raised) and "gingugu remote login" in str(raised)
    assert "not-the-token" not in str(raised)


async def test_a_pre_initialize_probe_is_refused_locally_and_initialize_still_lands(brain):
    # Claude Code >=2.1.287 sends `server/discover` before `initialize`. Forwarded,
    # the brain answers 400 (no session yet) and the link dies under the initialize.
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCError, JSONRPCMessage, JSONRPCRequest, JSONRPCResponse

    from gingugu import proxy

    def request(rid: int, method: str, params: dict) -> SessionMessage:
        req = JSONRPCRequest(jsonrpc="2.0", id=rid, method=method, params=params)
        return SessionMessage(JSONRPCMessage(req))

    init = {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0"},
    }
    c2p_send, c2p_recv = anyio.create_memory_object_stream(16)
    p2c_send, p2c_recv = anyio.create_memory_object_stream(16)
    async with serving(brain.app()) as served:
        with anyio.fail_after(15):
            async with anyio.create_task_group() as tg:
                tg.start_soon(proxy.run, served.url, brain.machine, c2p_recv, p2c_send)
                await c2p_send.send(request(0, "server/discover", {}))
                probe = (await p2c_recv.receive()).message.root
                await c2p_send.send(request(1, "initialize", init))
                reply = (await p2c_recv.receive()).message.root
                await c2p_send.aclose()
    assert isinstance(probe, JSONRPCError) and probe.id == 0
    assert probe.error.code == -32601  # method not found: the client falls back
    assert isinstance(reply, JSONRPCResponse) and reply.id == 1, reply
    assert reply.result["serverInfo"]["name"] == "gingugu"


async def test_client_closing_ends_the_proxy_cleanly(brain):
    async with serving(brain.app()) as served:
        with anyio.fail_after(15):
            async with through_proxy(served.url, brain.machine) as (session, outcome):
                await session.initialize()
    assert "raised" not in outcome, outcome.get("raised")
