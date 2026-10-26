"""``memory_context`` tells a persona how much board mail is waiting.

Session start is when an agent decides what to look at first, so the count
rides on the call every session already makes. Only a persona has an inbox:
an anonymous stdio caller and a warm minion get no ``board`` key.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "board.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "alpha")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PERSONA", "tyrone")
    monkeypatch.delenv("MEMORY_SERVER_ROLE", raising=False)
    from gingugu.server import build_server

    return build_server()


async def _call(server, tool, **args) -> dict:
    return _payload(await server.call_tool(tool, args))


async def test_context_counts_unread_mail_for_the_persona(server, monkeypatch):
    await _call(server, "board", action="post", body="one", to="beepboop")
    await _call(server, "board", action="post", body="two", to="*")
    await _call(server, "board", action="post", body="not yours", to="gemini")
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    out = await _call(server, "memory_context", namespace="alpha")
    assert out["ok"]
    assert out["board"] == {"unread": 2}


async def test_reading_a_post_takes_it_off_the_count(server, monkeypatch):
    sent = await _call(server, "board", action="post", body="one", to="beepboop")
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    await _call(server, "board", action="read", post_id=sent["post"]["id"])
    out = await _call(server, "memory_context", namespace="alpha")
    assert out["board"] == {"unread": 0}


async def test_no_persona_no_board_key(server, monkeypatch):
    monkeypatch.delenv("MEMORY_PERSONA")
    out = await _call(server, "memory_context", namespace="alpha")
    assert out["ok"] and "board" not in out


async def test_a_minion_gets_no_board_key(server, monkeypatch):
    monkeypatch.setenv("MEMORY_SERVER_ROLE", "minion")
    out = await _call(server, "memory_context", namespace="alpha")
    assert out["ok"] and "board" not in out
