"""An open task is unfinished business: reading it does not take it off the board.

Read receipts are kept per persona, so the first session of a persona to read a
post reads it for every other session of that persona. For a message that is
the point - read once. For a task it hid the work: Beepboop on one machine
posted two tasks, a Beepboop session read them in passing, and the session that
was meant to pick them up found an empty inbox. An open task stays unread until
someone claims it or it is done; a claim or a done is what acts on it.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from gingugu import board
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


def _post(conn, ns, author="tyrone", **kw):
    kw.setdefault("body", "ahoy")
    return board.post(conn, namespace_id=ns, author=author, **kw)


def _unread(conn, reader="beepboop", origin=None):
    return [p["id"] for p in board.inbox(conn, reader, origin=origin)]


def test_a_read_open_task_stays_unread(conn, ns):
    task = _post(conn, ns, kind="task")
    board.thread(conn, task["id"], "beepboop")
    assert _unread(conn) == [task["id"]]
    assert board.unread_count(conn, "beepboop") == 1


def test_a_read_message_still_leaves_the_inbox(conn, ns):
    msg = _post(conn, ns)
    board.thread(conn, msg["id"], "beepboop")
    assert _unread(conn) == []


def test_a_task_claimed_by_me_leaves_once_read(conn, ns):
    task = _post(conn, ns, kind="task")
    board.claim(conn, task["id"], "beepboop")
    assert _unread(conn) == [task["id"]]  # unread mail for its claimer, until read
    board.thread(conn, task["id"], "beepboop")
    assert _unread(conn) == []


def test_a_task_claimed_by_someone_else_leaves_even_unread(conn, ns):
    task = _post(conn, ns, kind="task")
    board.thread(conn, task["id"], "beepboop")
    board.claim(conn, task["id"], "minion")
    assert _unread(conn) == []


def test_a_done_task_leaves_even_unread(conn, ns):
    task = _post(conn, ns, kind="task")
    board.thread(conn, task["id"], "beepboop")
    board.done(conn, task["id"], "tyrone")
    assert _unread(conn) == []


def test_a_reply_on_an_open_task_thread_is_still_read_once(conn, ns):
    task = _post(conn, ns, kind="task")
    reply = _post(conn, ns, author="tyrone", parent_id=task["id"], recipient="beepboop")
    board.thread(conn, task["id"], "beepboop")
    assert _unread(conn) == [task["id"]]  # the task, not the reply
    assert reply["kind"] == "message"


def test_my_twins_task_survives_another_session_reading_it(conn, ns):
    # The live case: posted by Beepboop on mac-a, read by a Beepboop session in
    # passing, still waiting for the Beepboop session on mac-b.
    task = _post(conn, ns, author="beepboop", author_origin="mac-a", kind="task")
    board.thread(conn, task["id"], "beepboop")
    assert _unread(conn, origin="mac-b") == [task["id"]]
    assert board.unread_count(conn, "beepboop", origin="mac-b") == 1
