"""Board replies routed to the agent session that asked.

A persona can run several sessions on one machine; the inbox is per persona, so
a reply meant for one session used to reach all of them. A post now records its
writer's agent session (a hash of Claude Code's session id, never the raw id),
and a reply is routed to the session of the recipient's latest post in the
thread. That session sees it; other sessions of the persona see it once it has
gone unread for ``ROUTE_GRACE_SECONDS``, so a reply to a session that ended is
never lost. A reader whose session is unknown sees everything, as before.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from gingugu import agent_session, board
from gingugu.migrations import migrate
from gingugu.models import utcnow_iso

A = agent_session.key("11111111-aaaa-4aaa-8aaa-111111111111")
B = agent_session.key("22222222-bbbb-4bbb-8bbb-222222222222")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def ns(conn):
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'alpha', ?, ?)",
        (ns_id, now, now),
    )
    return ns_id


def _ids(posts) -> list[str]:
    return [p["id"] for p in posts]


def _thread(conn, ns, asker=A):
    """beepboop (session ``asker``) asks tyrone; tyrone replies."""
    ask = board.post(
        conn, namespace_id=ns, author="beepboop", recipient="tyrone", body="?", author_agent=asker
    )
    reply = board.post(conn, namespace_id=ns, author="tyrone", body="!", parent_id=ask["id"])
    return ask, reply


# --- identity ---------------------------------------------------------------


def test_key_hashes_and_never_returns_the_raw_id():
    raw = "11111111-aaaa-4aaa-8aaa-111111111111"
    assert A and len(A) == 32 and raw not in A
    assert agent_session.key(raw) == A  # stable


@pytest.mark.parametrize("bad", [None, "", 7, "x" * 129, "has space", "../etc", "a\nb"])
def test_key_refuses_malformed_ids(bad):
    assert agent_session.key(bad) is None


def test_local_reads_claude_codes_session_env(monkeypatch):
    monkeypatch.setenv(agent_session.ENV, "11111111-aaaa-4aaa-8aaa-111111111111")
    assert agent_session.current() == A  # stdio: no request in flight
    assert agent_session.headers() == {agent_session.HEADER: "11111111-aaaa-4aaa-8aaa-111111111111"}
    monkeypatch.delenv(agent_session.ENV)
    assert agent_session.current() is None
    assert agent_session.headers() == {}


# --- routing in the store ---------------------------------------------------


def test_a_reply_is_routed_to_the_asking_session(conn, ns):
    ask, reply = _thread(conn, ns)
    assert ask["author_agent"] == A
    assert reply["recipient"] == "beepboop"
    assert reply["recipient_agent"] == A


def test_only_the_asking_session_sees_a_fresh_reply(conn, ns):
    _, reply = _thread(conn, ns)
    assert _ids(board.inbox(conn, "beepboop", agent=A)) == [reply["id"]]
    assert _ids(board.inbox(conn, "beepboop", agent=B)) == []
    assert board.unread_count(conn, "beepboop", agent=A) == 1
    assert board.unread_count(conn, "beepboop", agent=B) == 0


def test_an_unknown_reader_session_sees_everything(conn, ns):
    _, reply = _thread(conn, ns)
    assert _ids(board.inbox(conn, "beepboop")) == [reply["id"]]
    assert _ids(board.inbox(conn, "beepboop", agent=None)) == [reply["id"]]


def test_an_unread_reply_reaches_every_session_after_the_grace(conn, ns):
    _, reply = _thread(conn, ns)
    old = datetime.now(UTC) - timedelta(seconds=board.ROUTE_GRACE_SECONDS + 60)
    conn.execute(
        "UPDATE board_posts SET created_at = ? WHERE id = ?", (old.isoformat(), reply["id"])
    )
    assert _ids(board.inbox(conn, "beepboop", agent=B)) == [reply["id"]]


def test_a_top_level_post_reaches_every_session(conn, ns):
    p = board.post(conn, namespace_id=ns, author="tyrone", recipient="beepboop", body="hi")
    assert p["recipient_agent"] is None
    assert _ids(board.inbox(conn, "beepboop", agent=B)) == [p["id"]]


def test_an_asker_with_no_known_session_is_not_routed(conn, ns):
    _, reply = _thread(conn, ns, asker=None)
    assert reply["recipient_agent"] is None
    assert _ids(board.inbox(conn, "beepboop", agent=B)) == [reply["id"]]


def test_a_reply_goes_to_the_session_of_the_post_it_answers(conn, ns):
    # Review repro: routing by the recipient's LATEST post sent a reply to A's
    # post to B, because B had spoken in the thread since.
    ask, _ = _thread(conn, ns, asker=A)
    more = board.post(
        conn, namespace_id=ns, author="beepboop", body="more", parent_id=ask["id"], author_agent=B
    )
    to_a = board.post(conn, namespace_id=ns, author="tyrone", body="ok", parent_id=ask["id"])
    to_b = board.post(conn, namespace_id=ns, author="tyrone", body="ok", parent_id=more["id"])
    assert to_a["recipient_agent"] == A
    assert to_b["recipient_agent"] == B


def test_a_twin_on_another_machine_answering_reaches_the_asker(conn, ns):
    # Review repro: B (same persona, other machine) answering A's post was
    # routed to B's own latest post, hiding the reply from A for the grace.
    ask = board.post(
        conn,
        namespace_id=ns,
        author="beepboop",
        recipient="tyrone",
        body="?",
        author_agent=A,
        author_origin="mac-a",
    )
    board.post(
        conn,
        namespace_id=ns,
        author="beepboop",
        body="me too",
        parent_id=ask["id"],
        author_agent=B,
        author_origin="mac-b",
    )
    reply = board.post(
        conn,
        namespace_id=ns,
        author="beepboop",
        body="answer",
        parent_id=ask["id"],
        author_agent=B,
        author_origin="mac-b",
    )
    assert reply["recipient"] == "beepboop"
    assert reply["recipient_agent"] == A
    inbox_a = [p["id"] for p in board.inbox(conn, "beepboop", origin="mac-a", agent=A)]
    assert reply["id"] in inbox_a  # "me too" is a twin reply to A as well


def test_an_asker_nudging_their_own_thread_routes_to_who_answered(conn, ns):
    ask, answer = _thread(conn, ns, asker=A)
    conn.execute("UPDATE board_posts SET author_agent = ? WHERE id = ?", (B, answer["id"]))
    nudge = board.post(
        conn,
        namespace_id=ns,
        author="beepboop",
        body="any news?",
        parent_id=ask["id"],
        author_agent=A,
    )
    assert nudge["recipient"] == "tyrone"
    assert nudge["recipient_agent"] == B


def test_a_broadcast_reply_is_never_routed(conn, ns):
    ask, _ = _thread(conn, ns)
    r = board.post(
        conn, namespace_id=ns, author="tyrone", body="all", parent_id=ask["id"], recipient="*"
    )
    assert r["recipient_agent"] is None


# --- the surfaces -----------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "board.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "alpha")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.delenv("MEMORY_SERVER_ROLE", raising=False)
    from gingugu.server import build_server

    return build_server()


async def _board(server, **args) -> dict:
    import json

    return json.loads((await server.call_tool("board", args)).content[0].text)


async def test_the_tool_routes_by_the_callers_session(server, monkeypatch):
    me = "11111111-aaaa-4aaa-8aaa-111111111111"
    other = "22222222-bbbb-4bbb-8bbb-222222222222"
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    monkeypatch.setenv(agent_session.ENV, me)
    ask = await _board(server, action="post", body="?", to="tyrone")
    assert ask["post"]["author_agent"] == A
    monkeypatch.setenv("MEMORY_PERSONA", "tyrone")
    monkeypatch.delenv(agent_session.ENV)
    await _board(server, action="reply", post_id=ask["post"]["id"], body="!")
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    monkeypatch.setenv(agent_session.ENV, other)
    assert (await _board(server, action="inbox"))["posts"] == []
    monkeypatch.setenv(agent_session.ENV, me)
    assert len((await _board(server, action="inbox"))["posts"]) == 1


def test_the_prompt_hook_filters_by_its_session(tmp_path, conn, ns):
    from gingugu import board_hook

    _, reply = _thread(conn, ns)
    db = tmp_path / "brain.db"
    conn.commit()
    conn.execute(f"VACUUM INTO '{db}'")
    assert _ids(board_hook.unread_local(db, "beepboop", None, set(), agent=A)) == [reply["id"]]
    assert board_hook.unread_local(db, "beepboop", None, set(), agent=B) == []


def test_the_remote_inbox_route_accepts_a_session():
    from gingugu import serve_hooks

    raw = "11111111-aaaa-4aaa-8aaa-111111111111"
    parsed = serve_hooks._parse_inbox(
        {"persona": "beepboop", "namespaces": None, "shown": [], "agent_session": raw}
    )
    assert A in parsed
    bare = serve_hooks._parse_inbox({"persona": "beepboop", "namespaces": None, "shown": []})
    assert A not in bare


def test_the_proxy_sends_its_session_to_the_brain(monkeypatch):
    from gingugu import proxy_auth

    monkeypatch.setenv(agent_session.ENV, "11111111-aaaa-4aaa-8aaa-111111111111")
    tokens = proxy_auth.SessionTokens("http://brain.test", "owner", {})
    client = proxy_auth.brain_client("http://brain.test", tokens)
    assert client.headers.get(agent_session.HEADER) == "11111111-aaaa-4aaa-8aaa-111111111111"


def test_over_http_the_session_comes_from_the_request_header(monkeypatch):
    from gingugu import inbound

    raw = "11111111-aaaa-4aaa-8aaa-111111111111"
    monkeypatch.setenv(agent_session.ENV, "22222222-bbbb-4bbb-8bbb-222222222222")  # the brain's own
    request = type("R", (), {"headers": {agent_session.HEADER: raw}})()
    monkeypatch.setattr(inbound, "current", lambda: inbound.Inbound(request, None))
    assert agent_session.current() == A  # the caller's, never the server process's
    request.headers = {}
    assert agent_session.current() is None
