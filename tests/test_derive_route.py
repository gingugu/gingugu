"""`POST /token/derive` on `gingugu serve`, and derived tokens fencing MCP calls.

Only an owner token may derive - the serve token or a machine's owner token -
never a scoped token and never a derived one, so a derived token cannot
extend itself. The derived token then carries its grant and home namespace
through to the tool chokepoints.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket

import httpx
import pytest
from starlette.testclient import TestClient

from gingugu.derived_tokens import DerivedTokens
from gingugu.serve_tokens import TokenStore
from tests.test_embeddings import FakeEmbedder

SERVE_TOKEN = "serve-owner-" + "s" * 32
DERIVE = "/token/derive"


class Clock:
    now = 5000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """The production app (build_app) over a throwaway brain."""
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "brain.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "server-default")
    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    from gingugu.serve import build_app
    from gingugu.server import build_server

    tokens = TokenStore(tmp_path / "serve_tokens.json")
    clock = Clock()
    derived = DerivedTokens(clock=clock)
    app = build_app(build_server(transport="http"), SERVE_TOKEN, tokens, derived)

    class Rig:
        pass

    r = Rig()
    r.app, r.tokens, r.derived, r.clock = app, tokens, derived, clock
    r.machine = tokens.add_owner("macbookpro")
    r.scoped = tokens.add("minion", {"alpha": "write"})
    return r


def _derive(client, token: str | None, body) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if isinstance(body, str):
        return client.post(DERIVE, headers=headers, content=body)
    return client.post(DERIVE, headers=headers, json=body)


# --- who may derive ----------------------------------------------------------


@pytest.mark.parametrize("who", ["serve", "machine"])
def test_owner_tokens_may_derive(rig, who):
    owner = SERVE_TOKEN if who == "serve" else rig.machine
    with TestClient(rig.app) as client:
        resp = _derive(client, owner, {"grant": "tyrone=write,crow=read", "home": "tyrone"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_in"] == 3600
    grant = rig.derived.resolve(body["token"])
    assert grant.home == "tyrone" and grant.can_write("tyrone") and not grant.can_write("crow")
    assert resp.headers.get("cache-control") == "no-store"


def test_no_grant_means_full_access_with_a_home(rig):
    with TestClient(rig.app) as client:
        resp = _derive(client, SERVE_TOKEN, {"home": "gingugu", "ttl": 120})
    assert resp.status_code == 200
    grant = rig.derived.resolve(resp.json()["token"])
    assert grant.is_full and grant.home == "gingugu"
    assert resp.json()["expires_in"] == 120


def test_scoped_and_derived_tokens_may_not_derive(rig):
    with TestClient(rig.app) as client:
        assert _derive(client, rig.scoped, {"home": "alpha"}).status_code == 403
        child = _derive(client, SERVE_TOKEN, {"home": "gingugu"}).json()["token"]
        assert _derive(client, child, {"home": "gingugu"}).status_code == 403
        assert _derive(client, None, {"home": "gingugu"}).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        [],
        {"grant": "tyrone"},  # no level
        {"grant": 7},
        {"grant": "tyrone=write", "home": "crow"},  # home outside the grant
        {"home": ""},
        {"home": "bad name!"},
        {"home": "gingugu", "ttl": 0},
        {"home": "gingugu", "ttl": 3601},
        {"home": "gingugu", "ttl": True},
        {"home": "gingugu", "ttl": "60"},
    ],
)
def test_bad_requests_are_400_and_mint_nothing(rig, body):
    with TestClient(rig.app) as client:
        resp = _derive(client, SERVE_TOKEN, body)
    assert resp.status_code == 400, resp.text
    assert rig.derived.live() == 0


def test_expired_derived_token_is_401_on_mcp(rig):
    with TestClient(rig.app) as client:
        token = _derive(client, SERVE_TOKEN, {"home": "gingugu", "ttl": 60}).json()["token"]
        rig.clock.now += 61
        resp = client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={})
    assert resp.status_code == 401


# --- end to end over a real socket -------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.asynccontextmanager
async def _serving(app):
    import uvicorn

    port = _free_port()
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(uv.serve())
    while not uv.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        uv.should_exit = True
        await task


async def _session_calls(base: str, token: str, calls: list[tuple[str, dict]]) -> list[dict]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    out = []
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        async with streamable_http_client(f"{base}/mcp", http_client=client) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                for tool, args in calls:
                    result = await session.call_tool(tool, args)
                    out.append(json.loads(result.content[0].text))
    return out


async def _derive_async(base: str, owner: str, body: dict) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base}{DERIVE}", headers={"Authorization": f"Bearer {owner}"}, json=body
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


async def test_persona_writes_home_by_default_and_cannot_write_crow(rig):
    async with _serving(rig.app) as base:
        # Seed crow as the owner so the persona has something to read.
        await _session_calls(
            base,
            SERVE_TOKEN,
            [
                (
                    "memory_store",
                    {"title": "rule", "content": "owl", "type": "fact", "namespace": "crow"},
                )
            ],
        )
        token = await _derive_async(
            base, rig.machine, {"grant": "tyrone=write,crow=read", "home": "tyrone"}
        )
        mine, theirs, read = await _session_calls(
            base,
            token,
            [
                ("memory_store", {"title": "t", "content": "kraken", "type": "fact"}),
                (
                    "memory_store",
                    {"title": "t", "content": "x", "type": "fact", "namespace": "crow"},
                ),
                ("memory_recall", {"query": "owl", "namespace": "crow"}),
            ],
        )
    assert mine["ok"] and mine["namespace"] == "tyrone"
    assert theirs["ok"] is False
    assert read["ok"] and read["count"] >= 1


async def test_full_derived_token_defaults_to_its_home_not_the_servers(rig):
    async with _serving(rig.app) as base:
        token = await _derive_async(base, SERVE_TOKEN, {"home": "gingugu"})
        stored, recalled = await _session_calls(
            base,
            token,
            [
                ("memory_store", {"title": "t", "content": "anchor", "type": "fact"}),
                ("memory_recall", {"query": "anchor"}),
            ],
        )
    assert stored["ok"] and stored["namespace"] == "gingugu"
    # Found in the home scope itself: a read scoped to the server's namespace
    # would come up empty and widen, which the reply would report.
    assert recalled["ok"] and recalled["count"] >= 1
    assert "widened_from" not in recalled
