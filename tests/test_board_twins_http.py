"""Twins over real HTTP: one persona, two machines, each with its own owner token.

The origin is never claimed by a caller: ``/token/derive`` stamps the derived
token with the name of the owner token that asked for it, and the board tool
and ``/hook/inbox`` read the machine from the token that sent the call.
"""

from __future__ import annotations

import json

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from gingugu.serve_tokens import TokenStore
from tests.proxy_fixtures import make_brain, serving


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


async def _derive(served, owner: str) -> str:
    async with httpx2.AsyncClient(timeout=10) as http:
        r = await http.post(
            f"{served.url}/token/derive",
            json={"name": "beepboop"},
            headers={"Authorization": f"Bearer {owner}"},
        )
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _call(served, token: str, tool: str, **args) -> dict:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
    async with http, Client(streamable_http_client(f"{served.url}/mcp", http_client=http)) as c:
        return _payload(await c.call_tool(tool, args))


async def _hook_inbox(served, owner: str) -> list[str]:
    async with httpx2.AsyncClient(timeout=10) as http:
        r = await http.post(
            f"{served.url}/hook/inbox",
            json={"persona": "beepboop", "namespaces": None, "shown": []},
            headers={"Authorization": f"Bearer {owner}"},
        )
    assert r.status_code == 200, r.text
    return [p["id"] for p in r.json()["posts"]]


async def test_beepboop_on_one_mac_reaches_beepboop_on_the_other(tmp_path, monkeypatch):
    brain = make_brain(tmp_path, monkeypatch, credentials=False)
    mac_two = TokenStore(tmp_path / "serve_tokens.json").add_owner("mac-two")
    async with serving(brain.app()) as served:
        here, there = await _derive(served, brain.machine), await _derive(served, mac_two)

        sent = await _call(served, here, "board", action="post", body="ahoy twin", to="beepboop")
        assert sent["ok"], sent
        assert sent["post"]["author"] == "beepboop"
        assert sent["post"]["author_origin"] == "macbookpro"

        theirs = await _call(served, there, "board", action="inbox")
        assert [p["id"] for p in theirs["posts"]] == [sent["post"]["id"]]
        mine = await _call(served, here, "board", action="inbox")
        assert mine["posts"] == []

        ctx = await _call(served, there, "memory_context", namespace="crow")
        assert ctx["board"] == {"unread": 1}

        assert await _hook_inbox(served, mac_two) == [sent["post"]["id"]]
        assert await _hook_inbox(served, brain.machine) == []

        reply = await _call(
            served, there, "board", action="reply", post_id=sent["post"]["id"], body="aye"
        )
        assert reply["post"]["recipient"] == "beepboop"
        assert reply["post"]["author_origin"] == "mac-two"
        back = await _call(served, here, "board", action="inbox")
        assert [p["id"] for p in back["posts"]] == [reply["post"]["id"]]


async def test_a_caller_cannot_claim_an_origin(tmp_path, monkeypatch):
    brain = make_brain(tmp_path, monkeypatch, credentials=False)
    async with serving(brain.app()) as served:
        async with httpx2.AsyncClient(timeout=10) as http:
            r = await http.post(
                f"{served.url}/token/derive",
                json={"name": "beepboop", "origin": "someone-else"},
                headers={"Authorization": f"Bearer {brain.machine}"},
            )
        token = r.json()["token"]
        sent = await _call(served, token, "board", action="post", body="hi", to="tyrone")
        assert sent["post"]["author_origin"] == "macbookpro"
