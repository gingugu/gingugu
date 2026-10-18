"""The request scope that replaced the SDK 1.x ``request_ctx`` ContextVar.

``inbound.middleware`` is what the fence and the access log now read, so these
pin its contract (set for the message, gone after, even on a raise) and the two
SDK 2.x behaviours the port leaned on: session identity over real HTTP, and
`gingugu serve` accepting bodies past the SDK's 4 MiB default.
"""

from __future__ import annotations

import sqlite3

import anyio
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from gingugu import inbound
from tests.proxy_fixtures import SERVE_TOKEN, make_brain, payload, serving


class _Ctx:
    def __init__(self, request) -> None:
        self.request = request


async def test_the_message_is_in_scope_for_its_handler_and_gone_after():
    seen = []

    async def handler(ctx):
        seen.append(inbound.current())
        return "done"

    assert await inbound.middleware(_Ctx(None), handler) == "done"
    assert seen[0] is not None and seen[0].request is None
    assert inbound.current() is None


async def test_a_raising_handler_still_leaves_nothing_behind():
    async def handler(ctx):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await inbound.middleware(_Ctx(None), handler)
    assert inbound.current() is None


async def _store_and_recall(url: str, title: str) -> None:
    headers = {"Authorization": f"Bearer {SERVE_TOKEN}"}
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(f"{url}/mcp", http_client=http) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                stored = payload(
                    await session.call_tool(
                        "memory_store", {"content": title, "title": title, "type": "fact"}
                    )
                )
                assert stored["ok"], stored
                for _ in range(2):
                    await session.call_tool("memory_recall", {"query": title})


async def test_each_http_session_keeps_its_own_access_log_id(tmp_path, monkeypatch):
    """One id across a session's calls, a different one per session.

    SDK 2.x builds a fresh ServerSession per message, so keying on the session
    object (as under 1.x) would give every call its own id.
    """
    brain = make_brain(tmp_path, monkeypatch)
    async with serving(brain.app()) as served:
        with anyio.fail_after(30):
            await _store_and_recall(served.url, "alpha session marker")
            await _store_and_recall(served.url, "bravo session marker")

    conn = sqlite3.connect(tmp_path / "brain.db")
    contexts = [row[0] for row in conn.execute("SELECT context FROM access_log ORDER BY rowid")]
    conn.close()
    assert contexts and None not in contexts, contexts
    # Session one's rows, then session two's: one id per run, two runs.
    runs = [ctx for i, ctx in enumerate(contexts) if i == 0 or contexts[i - 1] != ctx]
    assert len(runs) == 2 and runs[0] != runs[1], contexts


def _initialize(padding: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "big", "version": "0"},
            "_meta": {"padding": "x" * padding},
        },
    }


_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
_FIVE_MIB = 5 * 1024 * 1024


def _status(app, body: dict) -> int:
    with TestClient(app, base_url="http://localhost:8765") as client:
        return client.post("/mcp", json=body, headers=_HEADERS).status_code


def test_serve_accepts_a_body_past_the_sdk_default(tmp_path, monkeypatch):
    """A whole-brain memory_import is one request, and outgrows 4 MiB."""
    make_brain(tmp_path, monkeypatch)
    from gingugu.serve import mcp_app
    from gingugu.server import build_server

    # The SDK's own default refuses it: what this test guards against.
    assert (
        _status(build_server(transport="http").streamable_http_app(), _initialize(_FIVE_MIB)) == 413
    )
    assert _status(mcp_app(build_server(transport="http")), _initialize(_FIVE_MIB)) == 200
