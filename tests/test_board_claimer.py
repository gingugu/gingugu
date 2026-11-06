"""A claim says which session took the task, and every surface asks for one.

A task posted to the board was being built by one Beepboop session while it
stayed open, so a second Beepboop session was shown it as new work. Two gaps:
nothing told the working session to claim it, and a claim named only the
persona - so even a claimed task could not say which of a persona's sessions
held it. A claim now records the claimer's machine and agent session, a twin's
claim takes the task out of my inbox, and the hook, ``board read`` and the
startup contract all say to claim before working.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from gingugu import board, board_hook
from gingugu.migrations import migrate
from gingugu.models import utcnow_iso


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
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'ns', ?, ?)",
        (ns_id, now, now),
    )
    return ns_id


def _task(conn, ns, author="tyrone"):
    return board.post(conn, namespace_id=ns, author=author, body="fix it", kind="task")


def _unread(conn, reader="beepboop", origin=None, agent=None):
    return [p["id"] for p in board.inbox(conn, reader, origin=origin, agent=agent)]


# --- the store ---------------------------------------------------------------


def test_a_claim_records_the_claimers_machine_and_session(conn, ns):
    task = _task(conn, ns)
    won = board.claim(conn, task["id"], "beepboop", origin="mac-a", agent="sess-a")
    assert won["claimed_by"] == "beepboop"
    assert won["claimed_origin"] == "mac-a" and won["claimed_agent"] == "sess-a"
    assert board.thread(conn, task["id"], "tyrone")["post"]["claimed_agent"] == "sess-a"


def test_a_claim_without_a_known_session_still_works(conn, ns):
    won = board.claim(conn, _task(conn, ns)["id"], "beepboop")
    assert won["status"] == "claimed"
    assert won["claimed_origin"] is None and won["claimed_agent"] is None


def test_a_twin_sessions_claim_takes_the_task_out_of_my_inbox_even_unread(conn, ns):
    # The live case: two Beepboop sessions, one working the task.
    task = _task(conn, ns)
    board.claim(conn, task["id"], "beepboop", agent="sess-a")
    assert _unread(conn, agent="sess-b") == []
    assert board.unread_count(conn, "beepboop", agent="sess-b") == 0


def test_my_own_sessions_claim_is_still_mine_until_read(conn, ns):
    task = _task(conn, ns)
    board.claim(conn, task["id"], "beepboop", agent="sess-a")
    assert _unread(conn, agent="sess-a") == [task["id"]]


def test_an_unknown_claimer_or_reader_keeps_the_old_rule(conn, ns):
    old = _task(conn, ns)
    board.claim(conn, old["id"], "beepboop")  # claimer unknown
    assert _unread(conn, agent="sess-b") == [old["id"]]
    new = _task(conn, ns)
    board.claim(conn, new["id"], "beepboop", agent="sess-a")
    assert new["id"] in _unread(conn)  # reader unknown


def test_reclaiming_my_own_claim_is_not_an_error(conn, ns):
    task = _task(conn, ns)
    board.claim(conn, task["id"], "beepboop", origin="mac-a", agent="sess-a")
    again = board.claim(conn, task["id"], "beepboop", origin="mac-a", agent="sess-a")
    assert again["status"] == "claimed" and again["claimed_agent"] == "sess-a"


def test_a_twins_claim_names_the_holder_when_i_lose(conn, ns):
    task = _task(conn, ns)
    board.claim(conn, task["id"], "beepboop", origin="mac-a", agent="0123456789abcdef")
    with pytest.raises(board.BoardError) as lost:
        board.claim(conn, task["id"], "beepboop", origin="mac-b", agent="fedcba9876543210")
    message = str(lost.value)
    assert "already claimed by beepboop" in message
    assert "mac-a" in message and "01234567" in message


def test_an_unknown_session_never_counts_as_the_same_session(conn, ns):
    task = _task(conn, ns)
    board.claim(conn, task["id"], "beepboop")
    with pytest.raises(board.BoardError, match="already claimed"):
        board.claim(conn, task["id"], "beepboop")


def test_the_claimer_from_another_session_may_still_finish_it(conn, ns):
    # A dead session must not strand its task: done stays by persona.
    task = _task(conn, ns)
    board.claim(conn, task["id"], "beepboop", agent="sess-a")
    assert board.done(conn, task["id"], "beepboop")["status"] == "done"


# --- the prompt hook ---------------------------------------------------------


def _hook_post(**over):
    post = {
        "id": "t1",
        "author": "tyrone",
        "namespace": "ns",
        "kind": "task",
        "status": "open",
        "body": "fix it",
    }
    post.update(over)
    return post


def test_the_hook_tells_an_open_task_to_be_claimed():
    text = board_hook.render([_hook_post()])
    assert 'board(action="claim", post_id="t1")' in text
    assert "every other session" in text


def test_the_hook_says_who_holds_a_claimed_task():
    post = _hook_post(status="claimed", claimed_by="beepboop", claimed_origin="mac-a")
    text = board_hook.render([post])
    assert 'action="claim"' not in text
    assert "claimed by beepboop on mac-a" in text


def test_a_message_gets_no_claim_line():
    text = board_hook.render([_hook_post(kind="message")])
    assert 'action="claim"' not in text
