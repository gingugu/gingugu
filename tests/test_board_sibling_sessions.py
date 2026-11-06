"""Board mail between two sessions of one persona on one machine.

One persona often runs several sessions on the same machine. A post records
its writer's agent session, so a sibling session - same persona, same machine,
different session - is told apart from the session that wrote it: the sibling
gets the post as mail, the writer never does. A session that is unknown on
either side keeps the persona as the identity, exactly as before, so a watcher
without a session id never echoes its own agent's posts.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from gingugu import agent_session, board, board_hook
from gingugu.migrations import migrate
from gingugu.models import utcnow_iso

A = agent_session.key("11111111-aaaa-4aaa-8aaa-111111111111")
B = agent_session.key("22222222-bbbb-4bbb-8bbb-222222222222")
MAC = "mac-a"


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


def _post(conn, ns, agent, origin=MAC, **kw):
    return board.post(
        conn,
        namespace_id=ns,
        author="beepboop",
        body=kw.pop("body", "hi"),
        author_origin=origin,
        author_agent=agent,
        **kw,
    )


def _ids(posts) -> list[str]:
    return [p["id"] for p in posts]


@pytest.mark.parametrize("origin", [MAC, None])  # remote mode, and a local stdio brain
def test_a_sibling_session_on_my_machine_gets_my_post(conn, ns, origin):
    made = _post(conn, ns, A, origin=origin)
    assert _ids(board.inbox(conn, "beepboop", origin=origin, agent=B)) == [made["id"]]
    assert board.unread_count(conn, "beepboop", origin, B) == 1


@pytest.mark.parametrize("origin", [MAC, None])
def test_the_session_that_wrote_a_post_never_gets_it(conn, ns, origin):
    _post(conn, ns, A, origin=origin)
    assert board.inbox(conn, "beepboop", origin=origin, agent=A) == []
    assert board.unread_count(conn, "beepboop", origin, A) == 0


@pytest.mark.parametrize("post_agent, reader_agent", [(None, B), (A, None), (None, None)])
def test_an_unknown_session_keeps_the_persona_rule(conn, ns, post_agent, reader_agent):
    _post(conn, ns, post_agent)
    assert board.inbox(conn, "beepboop", origin=MAC, agent=reader_agent) == []


def test_a_sibling_broadcast_and_a_directed_post_both_arrive(conn, ns):
    broadcast = _post(conn, ns, A)
    direct = _post(conn, ns, A, recipient="beepboop")
    got = _ids(board.inbox(conn, "beepboop", origin=MAC, agent=B))
    assert got == [broadcast["id"], direct["id"]]


def test_is_me_tells_sessions_apart():
    assert board.is_me("beepboop", MAC, A, "beepboop", MAC, A)
    assert not board.is_me("beepboop", MAC, A, "beepboop", MAC, B)
    assert board.is_me("beepboop", MAC, None, "beepboop", MAC, B)  # unknown: persona wins
    assert board.is_me("beepboop", MAC, A, "beepboop", MAC, None)
    assert not board.is_me("beepboop", "mac-b", A, "beepboop", MAC, A)  # a twin machine
    assert not board.is_me("tyrone", MAC, A, "beepboop", MAC, A)


def test_a_reply_to_my_siblings_post_goes_back_to_that_sibling(conn, ns):
    ask = _post(conn, ns, A, body="anyone?")
    reply = _post(conn, ns, B, body="me", parent_id=ask["id"])
    assert reply["recipient"] == "beepboop"
    assert reply["recipient_agent"] == A
    assert _ids(board.inbox(conn, "beepboop", origin=MAC, agent=A)) == [reply["id"]]
    assert _ids(board.inbox(conn, "beepboop", origin=MAC, agent=B)) == [ask["id"]]


def test_my_sibling_answers_back_into_its_own_thread(conn, ns):
    ask = _post(conn, ns, A, body="anyone?")
    reply = _post(conn, ns, B, body="me", parent_id=ask["id"])
    back = _post(conn, ns, A, body="thanks", parent_id=reply["id"])
    assert back["recipient"] == "beepboop"
    assert back["recipient_agent"] == B


def test_replying_in_my_own_thread_still_nudges_the_other_party(conn, ns):
    ask = _post(conn, ns, A, recipient="tyrone", body="?")
    board.post(conn, namespace_id=ns, author="tyrone", body="!", parent_id=ask["id"])
    again = _post(conn, ns, A, body="and?", parent_id=ask["id"])
    assert again["recipient"] == "tyrone"


def test_the_prompt_hook_reads_sibling_mail(tmp_path, ns, conn):
    """``unread_local`` - the stdio hook and the brain's /hook/inbox both run it."""
    db = tmp_path / "brain.db"
    disk = sqlite3.connect(db)
    disk.row_factory = sqlite3.Row
    migrate(disk)
    now = utcnow_iso()
    disk.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'alpha', ?, ?)",
        (ns, now, now),
    )
    made = _post(disk, ns, A)
    disk.commit()
    disk.close()
    got = board_hook.unread_local(db, "beepboop", None, set(), origin=MAC, agent=B)
    assert _ids(got) == [made["id"]]
    assert board_hook.unread_local(db, "beepboop", None, set(), origin=MAC, agent=A) == []
