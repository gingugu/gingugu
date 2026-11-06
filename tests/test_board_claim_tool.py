"""The ``board`` tool's side of claiming: who claimed, and the nudge to claim.

The handler is where a claimer's identity is decided, as it is for an author:
the agent session comes from Claude Code's session id, never an argument.
``read`` is the moment an agent decides to work a task, so reading an open
task's thread says to claim it first.
"""

from __future__ import annotations

import json

import pytest

from gingugu import agent_session


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "board.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "alpha")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    monkeypatch.setenv(agent_session.ENV, "session-a")
    monkeypatch.delenv("MEMORY_SERVER_ROLE", raising=False)
    from gingugu.server import build_server

    return build_server()


async def _board(server, **args) -> dict:
    result = await server.call_tool("board", args)
    return json.loads(result.content[0].text)


async def _task(server) -> str:
    out = await _board(server, action="post", body="fix it", kind="task", to="beepboop")
    return out["post"]["id"]


async def test_a_claim_records_this_agent_session(server):
    task = await _task(server)
    out = await _board(server, action="claim", post_id=task)
    assert out["ok"], out
    assert out["post"]["claimed_agent"] == agent_session.key("session-a")


async def test_a_twin_session_loses_the_claim_and_drops_it_from_its_inbox(server, monkeypatch):
    task = await _task(server)
    await _board(server, action="claim", post_id=task)
    monkeypatch.setenv(agent_session.ENV, "session-b")
    lost = await _board(server, action="claim", post_id=task)
    assert not lost["ok"] and "already claimed by beepboop" in lost["error"]
    box = await _board(server, action="inbox")
    assert task not in [p["id"] for p in box["posts"]]


async def test_reading_an_open_task_says_to_claim_it(server):
    task = await _task(server)
    out = await _board(server, action="read", post_id=task)
    assert out["ok"], out
    assert f'board(action="claim", post_id="{task}")' in out["next"]


async def test_reading_a_reply_points_at_its_open_root_task(server):
    task = await _task(server)
    reply = await _board(server, action="reply", post_id=task, body="more detail", to="beepboop")
    out = await _board(server, action="read", post_id=reply["post"]["id"])
    assert f'post_id="{task}"' in out["next"]


async def test_reading_a_claimed_task_or_a_message_has_no_nudge(server):
    task = await _task(server)
    await _board(server, action="claim", post_id=task)
    assert "next" not in await _board(server, action="read", post_id=task)
    msg = await _board(server, action="post", body="ahoy", to="beepboop")
    assert "next" not in await _board(server, action="read", post_id=msg["post"]["id"])
