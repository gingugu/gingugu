"""`gingugu` in remote mode: a stdio <-> streamable-HTTP relay to the brain.

Every test drives a real ClientSession through ``proxy.run`` against a real
`gingugu serve` app on a loopback socket, so the bearer header, the session id
handshake and a server that goes away are all exercised over the wire.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.exceptions import McpError

from gingugu import proxy
from tests.test_embeddings import FakeEmbedder

TOKEN = "proxy-owner-token-" + "x" * 30
_CRED_TOOLS = {"credential_store", "credential_get", "credential_list", "credential_delete"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def brain_app(tmp_path, monkeypatch):
    """A `gingugu serve` app WITH credential tools on, so stripping is provable."""
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "brain.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "proxy-test")
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "true")
    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.serve import BearerAuthMiddleware
    from gingugu.server import build_server

    app = build_server(transport="http").streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=TOKEN, tokens=None)
    return app


class _Served:
    def __init__(self, uv, task, url):
        self.uv, self.task, self.url = uv, task, url

    async def stop(self) -> None:
        self.uv.should_exit = True
        self.uv.force_exit = True
        await self.task


@contextlib.asynccontextmanager
async def _serving(app):
    import uvicorn

    port = _free_port()
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(uv.serve())
    while not uv.started:
        await asyncio.sleep(0.01)
    served = _Served(uv, task, f"http://127.0.0.1:{port}")
    try:
        yield served
    finally:
        if not task.done():
            await served.stop()


@contextlib.asynccontextmanager
async def _through_proxy(url: str, token: str):
    """A ClientSession whose transport is ``proxy.run`` (in place of stdio).

    Yields (session, outcome); ``outcome`` holds what ``proxy.run`` returned or
    raised once it has finished.
    """
    c2p_send, c2p_recv = anyio.create_memory_object_stream(16)
    p2c_send, p2c_recv = anyio.create_memory_object_stream(16)
    outcome: dict = {}

    async def _run() -> None:
        try:
            outcome["returned"] = await proxy.run(url, token, c2p_recv, p2c_send)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            outcome["raised"] = exc
        finally:
            await p2c_send.aclose()

    async with anyio.create_task_group() as tg:
        tg.start_soon(_run)
        async with ClientSession(p2c_recv, c2p_send) as session:
            yield session, outcome
        await c2p_send.aclose()


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


# --- the relay ---------------------------------------------------------------


async def test_round_trip_store_then_recall(brain_app):
    async with _serving(brain_app) as served:
        async with _through_proxy(served.url, TOKEN) as (session, _):
            init = await session.initialize()
            assert init.serverInfo.name == "gingugu"
            stored = _payload(
                await session.call_tool(
                    "memory_store",
                    {"title": "proxied note", "content": "kraken tentacle almanac", "type": "fact"},
                )
            )
            assert stored["ok"]
            recalled = _payload(
                await session.call_tool("memory_recall", {"query": "kraken tentacle almanac"})
            )
            assert recalled["ok"] and "proxied note" in str(recalled)


async def test_tool_surface_is_the_servers_minus_credentials(brain_app):
    async with _serving(brain_app) as served:
        async with _through_proxy(served.url, TOKEN) as (session, _):
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
    assert "memory_store" in names and "memory_recall" in names
    assert not (names & _CRED_TOOLS)


@pytest.mark.parametrize("tool", sorted(_CRED_TOOLS))
async def test_credential_calls_are_refused_by_the_proxy(brain_app, tool):
    async with _serving(brain_app) as served:
        async with _through_proxy(served.url, TOKEN) as (session, _):
            await session.initialize()
            with pytest.raises(McpError) as err:
                await session.call_tool(tool, {"service_name": "x"})
            # The proxy's own refusal, not the server's: it names remote mode.
            assert "remote" in str(err.value).lower()
            # The session survives a refusal.
            assert _payload(await session.call_tool("memory_stats", {}))["ok"]


async def test_wrong_token_fails_loudly_not_a_hang(brain_app):
    async with _serving(brain_app) as served:
        with anyio.fail_after(15):
            async with _through_proxy(served.url, "not-the-token") as (session, outcome):
                with pytest.raises(McpError):
                    await session.initialize()
    assert "raised" in outcome, "proxy.run must raise when the brain refuses it"
    assert isinstance(outcome["raised"], proxy.ProxyLost)
    assert "not-the-token" not in str(outcome["raised"])


async def test_server_gone_mid_session_errors_the_call_not_a_hang(brain_app):
    async with _serving(brain_app) as served:
        with anyio.fail_after(20):
            async with _through_proxy(served.url, TOKEN) as (session, outcome):
                await session.initialize()
                await served.stop()
                with pytest.raises(McpError) as err:
                    await session.call_tool("memory_stats", {})
                assert TOKEN not in str(err.value)
    assert isinstance(outcome.get("raised"), proxy.ProxyLost)
    assert TOKEN not in str(outcome["raised"])


async def test_client_closing_ends_the_proxy_cleanly(brain_app):
    async with _serving(brain_app) as served:
        with anyio.fail_after(15):
            async with _through_proxy(served.url, TOKEN) as (session, outcome):
                await session.initialize()
    assert "raised" not in outcome, outcome.get("raised")
