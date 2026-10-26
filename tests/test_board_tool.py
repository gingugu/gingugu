"""The ``board`` MCP tool, its inbox resources, and the push that rides on them."""

from __future__ import annotations

import json

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.subscriptions import ResourceUpdated

from gingugu.grants import Grant
from gingugu.handlers.board import author_for
from gingugu.serve_tokens import TokenStore
from tests.proxy_fixtures import make_brain, serving

INBOX = "gingugu://board/inbox/"


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "board.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "alpha")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    monkeypatch.delenv("MEMORY_SERVER_ROLE", raising=False)
    from gingugu.server import build_server

    return build_server()


async def _board(server, **args) -> dict:
    return _payload(await server.call_tool("board", args))


async def test_the_author_is_the_persona_not_an_argument(server):
    out = await _board(server, action="post", body="ahoy", to="tyrone")
    assert out["ok"], out
    assert out["post"]["author"] == "beepboop"
    assert out["post"]["namespace"] == "alpha"
    tool = next(t for t in await server.list_tools() if t.name == "board")
    assert "author" not in tool.input_schema["properties"]


async def test_a_minion_posts_as_minion(server, monkeypatch):
    monkeypatch.setenv("MEMORY_SERVER_ROLE", "minion")
    out = await _board(server, action="post", body="found it")
    assert out["post"]["author"] == "minion"


async def test_round_trip_between_two_personas(server, monkeypatch):
    sent = await _board(server, action="post", body="claim this", to="tyrone", kind="task")
    monkeypatch.setenv("MEMORY_PERSONA", "tyrone")
    box = await _board(server, action="inbox")
    assert box["unread"] == 1 and [p["id"] for p in box["posts"]] == [sent["post"]["id"]]
    claimed = await _board(server, action="claim", post_id=sent["post"]["id"])
    assert claimed["post"]["claimed_by"] == "tyrone"
    replied = await _board(server, action="reply", post_id=sent["post"]["id"], body="on it")
    assert replied["post"]["recipient"] == "beepboop"
    assert (await _board(server, action="done", post_id=sent["post"]["id"]))["post"][
        "status"
    ] == "done"
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    thread = await _board(server, action="read", post_id=sent["post"]["id"])
    assert [r["body"] for r in thread["replies"]] == ["on it"]


@pytest.mark.parametrize(
    "args",
    [
        {"action": "bogus"},
        {"action": "post"},
        {"action": "read"},
        {"action": "claim", "post_id": "nope"},
    ],
)
async def test_bad_requests_are_structured_errors(server, args):
    out = await _board(server, **args)
    assert out["ok"] is False and out["error"]


async def test_inbox_resource_reads_as_json(server, monkeypatch):
    await _board(server, action="post", body="ahoy", to="tyrone")
    contents = list(await server.read_resource(f"{INBOX}tyrone"))
    data = json.loads(contents[0].content)
    assert data["persona"] == "tyrone"
    assert [p["body"] for p in data["posts"]] == ["ahoy"]


async def test_inbox_resource_refuses_a_malformed_persona(server):
    contents = list(await server.read_resource(f"{INBOX}bad!"))
    assert "error" in json.loads(contents[0].content)


async def test_a_post_wakes_a_listener_on_the_recipients_inbox(server):
    async with Client(server, mode="2026-07-28") as client:
        async with client.listen(resource_subscriptions=[f"{INBOX}tyrone", f"{INBOX}*"]) as sub:
            await client.call_tool("board", {"action": "post", "body": "ahoy", "to": "tyrone"})
            with anyio.fail_after(5):
                assert await sub.__anext__() == ResourceUpdated(uri=f"{INBOX}tyrone")
            await client.call_tool("board", {"action": "post", "body": "all hands"})
            with anyio.fail_after(5):
                assert await sub.__anext__() == ResourceUpdated(uri=f"{INBOX}*")


def test_author_for_http_grants():
    assert author_for("http", Grant("derived:beepboop", {"*": "write"}, derived=True)) == "beepboop"
    assert author_for("http", Grant("tyrone", {"alpha": "write"})) == "tyrone"


async def test_scoped_token_over_http_posts_as_itself_and_sees_only_its_board(
    tmp_path, monkeypatch
):
    brain = make_brain(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "MEMORY_PERSONA", "beepboop"
    )  # the brain's own env must not leak into authorship
    scoped = TokenStore(tmp_path / "serve_tokens.json").add("tyrone", {"alpha": "write"})
    async with serving(brain.app()) as served:

        async def client(token: str):
            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
            return http, Client(streamable_http_client(f"{served.url}/mcp", http_client=http))

        http, owner = await client(brain.machine)
        async with http, owner:
            for ns in ("alpha", "hidden"):
                await owner.call_tool("memory_namespaces", {"action": "create", "name": ns})
            for ns, body in (("alpha", "public"), ("hidden", "ZEBRA-SECRET")):
                out = _payload(
                    await owner.call_tool(
                        "board", {"action": "post", "body": body, "to": "tyrone", "namespace": ns}
                    )
                )
                assert out["ok"], out
                assert out["post"]["author"] == "macbookpro"
        http, tyrone = await client(scoped)
        async with http, tyrone:
            box = _payload(await tyrone.call_tool("board", {"action": "inbox"}))
            assert [p["body"] for p in box["posts"]] == ["public"]
            res = await tyrone.read_resource(f"{INBOX}tyrone")
            assert "ZEBRA-SECRET" not in res.contents[0].text
            assert [p["body"] for p in json.loads(res.contents[0].text)["posts"]] == ["public"]
            mine = _payload(
                await tyrone.call_tool(
                    "board", {"action": "post", "body": "hi", "namespace": "alpha"}
                )
            )
            assert mine["post"]["author"] == "tyrone"


def test_the_proxy_derives_under_the_persona(monkeypatch):
    from gingugu.proxy_auth import derive_name

    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    assert derive_name("gingugu") == "beepboop"
    monkeypatch.delenv("MEMORY_PERSONA")
    assert derive_name("gingugu") == "gingugu"


async def test_a_post_wakes_a_listener_over_http(tmp_path, monkeypatch):
    brain = make_brain(tmp_path, monkeypatch)
    async with serving(brain.app()) as served:
        headers = {"Authorization": f"Bearer {brain.machine}"}
        async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
            transport = streamable_http_client(f"{served.url}/mcp", http_client=http)
            async with Client(transport) as client:
                async with client.listen(resource_subscriptions=[f"{INBOX}tyrone"]) as sub:
                    await client.call_tool(
                        "board", {"action": "post", "body": "ahoy", "to": "tyrone"}
                    )
                    with anyio.fail_after(5):
                        assert await sub.__anext__() == ResourceUpdated(uri=f"{INBOX}tyrone")
