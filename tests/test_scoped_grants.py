"""Scoped tokens, the allowed side: what a grant DOES let through.

The canary proves nothing leaks; on its own it would also pass a fence that
refused everything. These pin the other half - read-only means readable,
write means writable, a granted namespace that does not exist yet can be
created - and run the whole chain once over real HTTP, header to grant.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import httpx
import pytest

from gingugu.grants import READ, WRITE, Grant
from tests.scoped_fixtures import HIDDEN, MARKER, call, scope_to


async def _ok(server, tool: str, args: dict) -> dict:
    out = await call(server, tool, args)
    assert out["ok"], (tool, out)
    return out


async def _refused(server, tool: str, args: dict, wording: str) -> None:
    out = await call(server, tool, args)
    assert out["ok"] is False, (tool, out)
    assert wording in out["error"], (tool, out)


async def test_write_grant_can_write_its_namespace(brain, monkeypatch):
    scope_to(monkeypatch)
    s, ids = brain.server, brain.ids
    stored = await _ok(
        s, "memory_store", {"namespace": "alpha", "title": "t", "content": "c", "type": "fact"}
    )
    await _ok(s, "memory_update", {"memory_id": ids["A1"], "content": "rewritten", "tags": "x"})
    await _ok(
        s,
        "memory_relate",
        {"source_id": ids["A2"], "target_id": stored["memory"]["id"], "relation_type": "caused_by"},
    )
    await _ok(s, "memory_unrelate", {"source_id": ids["A1"], "target_id": ids["A2"]})
    await _ok(s, "memory_forget", {"memory_id": ids["A2"], "hard_delete": True})


async def test_read_grant_reads_but_never_writes(brain, monkeypatch):
    scope_to(monkeypatch)
    s, ids = brain.server, brain.ids
    found = await _ok(s, "memory_recall", {"query": "gamma rollout", "namespace": "gamma"})
    assert [m["id"] for m in found["memories"]] == [ids["G1"]]
    await _ok(s, "memory_excerpt", {"memory_id": ids["G1"]})
    ro = "read-only for this token"
    await _refused(
        s, "memory_store", {"namespace": "gamma", "title": "t", "content": "c", "type": "fact"}, ro
    )
    await _refused(s, "memory_update", {"memory_id": ids["G1"], "content": "x"}, ro)
    await _refused(s, "memory_update", {"memory_id": ids["G1"], "tags": "x"}, ro)
    await _refused(s, "memory_forget", {"memory_id": ids["G1"]}, ro)
    await _refused(
        s,
        "memory_relate",
        {"source_id": ids["A1"], "target_id": ids["G1"], "relation_type": "caused_by"},
        ro,
    )
    await _refused(
        s,
        "memory_tripwire",
        {"action": "add", "memory_id": ids["G1"], "tool": "Bash", "pattern": "x"},
        ro,
    )
    after = await _ok(s, "memory_search", {"ids": ids["G1"]})
    assert after["memories"][0]["content"].startswith("gamma")


async def test_granted_namespace_is_created_on_first_write(brain, monkeypatch):
    scope_to(monkeypatch)
    s = brain.server
    await _ok(
        s, "memory_store", {"namespace": "scratch", "title": "t", "content": "c", "type": "fact"}
    )
    listed = await _ok(s, "memory_namespaces", {})
    assert {n["name"] for n in listed["namespaces"]} == {"alpha", "gamma", "scratch"}


async def test_wildcard_read_sees_everything_and_writes_only_where_granted(brain, monkeypatch):
    scope_to(monkeypatch, Grant("reader", {"*": READ, "scratch": WRITE}))
    s = brain.server
    hit = await _ok(s, "memory_recall", {"query": MARKER, "namespace": HIDDEN})
    assert hit["count"] >= 1
    await _refused(
        s,
        "memory_store",
        {"namespace": HIDDEN, "title": "t", "content": "c", "type": "fact"},
        "read-only",
    )
    await _ok(
        s, "memory_store", {"namespace": "scratch", "title": "t", "content": "c", "type": "fact"}
    )
    await _refused(s, "credential_list", {}, "not available to a scoped token")


async def test_full_grant_is_unfenced(brain, monkeypatch):
    scope_to(monkeypatch, Grant("owner", {"*": WRITE}))
    out = await _ok(brain.server, "memory_recall", {"query": MARKER, "namespace": HIDDEN})
    assert out["count"] >= 1
    await _ok(brain.server, "credential_list", {})


async def test_http_request_without_a_grant_is_refused(brain, monkeypatch):
    monkeypatch.setattr("gingugu.handlers.fence.request_grant", lambda _t: None)
    out = await call(brain.server, "memory_namespaces", {})
    assert out == {"ok": False, "error": "unauthorized"}


# --- end to end: Authorization header -> middleware -> grant -> fence --------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.asynccontextmanager
async def _serving(server, token_path):
    import uvicorn

    from gingugu.serve import BearerAuthMiddleware
    from gingugu.serve_tokens import TokenStore

    app = server.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token="owner-token", tokens=TokenStore(token_path))
    port = _free_port()
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(uv.serve())
    while not uv.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        uv.should_exit = True
        await task


async def _recall_over_http(url: str, token: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from tests.scoped_fixtures import payload

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("memory_recall", args)
                return payload(result.content)


async def test_scoped_token_over_real_http(brain, tmp_path):
    from gingugu.serve_tokens import TokenStore

    path = tmp_path / "serve_tokens.json"
    scoped = TokenStore(path).add("minion", {"alpha": WRITE})
    async with _serving(brain.server, path) as url:
        everywhere = {"query": "alpha deploy pipeline rollout", "include_related": True}
        mine = await _recall_over_http(url, scoped, everywhere)
        assert mine["ok"] and mine["count"] >= 1
        assert MARKER not in str(mine)
        owner = await _recall_over_http(url, "owner-token", {"query": MARKER, "namespace": HIDDEN})
        assert MARKER in str(owner)
        # Revoked from "another process" - refused on the next request, no restart.
        TokenStore(path).revoke("minion")
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers={"Authorization": f"Bearer {scoped}"}, json={})
        assert resp.status_code == 401


@pytest.mark.parametrize(
    "header", ["", "Bearer", "Bearer nope", "Basic owner-token", "owner-token"]
)
async def test_unknown_credentials_get_401_over_http(brain, tmp_path, header):
    async with _serving(brain.server, tmp_path / "serve_tokens.json") as url:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers={"Authorization": header}, json={})
    assert resp.status_code == 401
