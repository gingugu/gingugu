"""The agent board's store: post, inbox, thread, claim, done - and the fence on each."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from gingugu import board, grants
from gingugu.grants import READ, WRITE, Grant
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


def _ns(conn, name: str) -> str:
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (ns_id, name, now, now),
    )
    return ns_id


@pytest.fixture
def alpha(conn):
    return _ns(conn, "alpha")


@pytest.fixture
def hidden(conn):
    return _ns(conn, "hidden")


def _post(conn, ns_id, author="beepboop", **kw):
    kw.setdefault("body", "ahoy")
    return board.post(conn, namespace_id=ns_id, author=author, **kw)


# --- post -------------------------------------------------------------------


def test_post_defaults_to_an_open_broadcast_message(conn, alpha):
    p = _post(conn, alpha, author_session="s1")
    assert p["recipient"] == "*"
    assert p["kind"] == "message"
    assert p["status"] == "open"
    assert p["author"] == "beepboop"
    assert p["author_session"] == "s1"
    assert p["namespace"] == "alpha"
    assert p["parent_id"] is None
    assert p["claimed_by"] is None


@pytest.mark.parametrize(
    "kw",
    [
        {"kind": "memo"},
        {"body": ""},
        {"body": "   "},
        {"recipient": "not a persona!"},
        {"body": "x" * (board.MAX_BODY + 1)},
    ],
)
def test_post_rejects_bad_input(conn, alpha, kw):
    with pytest.raises(board.BoardError):
        _post(conn, alpha, **kw)


def test_post_to_an_unknown_parent_is_refused(conn, alpha):
    with pytest.raises(board.BoardError):
        _post(conn, alpha, parent_id="nope")


def test_reply_lands_in_the_parents_namespace_addressed_to_its_author(conn, alpha, hidden):
    root = _post(conn, alpha, author="tyrone", recipient="beepboop")
    reply = _post(conn, hidden, author="beepboop", parent_id=root["id"])
    assert reply["namespace"] == "alpha"
    assert reply["recipient"] == "tyrone"
    assert reply["kind"] == "message"


def test_reply_to_a_reply_hangs_off_the_root(conn, alpha):
    root = _post(conn, alpha, author="tyrone")
    r1 = _post(conn, alpha, author="beepboop", parent_id=root["id"])
    r2 = _post(conn, alpha, author="tyrone", parent_id=r1["id"])
    assert r2["parent_id"] == root["id"]
    assert r2["recipient"] == "beepboop"


# --- inbox ------------------------------------------------------------------


def test_inbox_shows_what_is_addressed_to_me_or_everyone(conn, alpha):
    mine = _post(conn, alpha, author="tyrone", recipient="beepboop")
    everyone = _post(conn, alpha, author="tyrone")
    _post(conn, alpha, author="tyrone", recipient="someone-else")
    ids = [p["id"] for p in board.inbox(conn, "beepboop")]
    assert ids == [mine["id"], everyone["id"]]  # oldest first


def test_inbox_never_shows_my_own_posts(conn, alpha):
    _post(conn, alpha, author="beepboop")
    assert board.inbox(conn, "beepboop") == []


def test_reading_a_thread_marks_it_read_and_drops_it_from_the_inbox(conn, alpha):
    root = _post(conn, alpha, author="tyrone", recipient="beepboop")
    assert board.unread_count(conn, "beepboop") == 1
    thread = board.thread(conn, root["id"], "beepboop")
    assert thread["post"]["id"] == root["id"]
    assert board.inbox(conn, "beepboop") == []
    assert board.unread_count(conn, "beepboop") == 0
    assert [p["id"] for p in board.inbox(conn, "beepboop", include_read=True)] == [root["id"]]


def test_reading_twice_is_harmless(conn, alpha):
    root = _post(conn, alpha, author="tyrone")
    board.thread(conn, root["id"], "beepboop")
    board.thread(conn, root["id"], "beepboop")
    assert board.unread_count(conn, "beepboop") == 0


def test_a_read_by_one_agent_is_not_a_read_by_another(conn, alpha):
    root = _post(conn, alpha, author="tyrone")
    board.thread(conn, root["id"], "beepboop")
    assert board.unread_count(conn, "crow-agent") == 1


def test_thread_returns_replies_oldest_first_and_marks_them_read(conn, alpha):
    root = _post(conn, alpha, author="beepboop")
    r1 = _post(conn, alpha, author="tyrone", parent_id=root["id"])
    r2 = _post(conn, alpha, author="tyrone", parent_id=root["id"])
    assert board.unread_count(conn, "beepboop") == 2
    thread = board.thread(conn, r1["id"], "beepboop")  # any post in a thread opens the thread
    assert thread["post"]["id"] == root["id"]
    assert [r["id"] for r in thread["replies"]] == [r1["id"], r2["id"]]
    assert board.unread_count(conn, "beepboop") == 0


def test_thread_of_an_unknown_post_is_an_error(conn):
    with pytest.raises(board.BoardError):
        board.thread(conn, "nope", "beepboop")


def test_inbox_limit_is_clamped(conn, alpha, monkeypatch):
    monkeypatch.setattr(board, "MAX_LIMIT", 3)
    for _ in range(5):
        _post(conn, alpha, author="tyrone")
    assert len(board.inbox(conn, "beepboop", limit=10**9)) == 3


def test_inbox_respects_limit(conn, alpha):
    for _ in range(5):
        _post(conn, alpha, author="tyrone")
    assert len(board.inbox(conn, "beepboop", limit=2)) == 2


# --- claim / done -----------------------------------------------------------


def test_claim_is_compare_and_set(conn, alpha):
    task = _post(conn, alpha, author="beepboop", kind="task")
    won = board.claim(conn, task["id"], "tyrone")
    assert won["status"] == "claimed" and won["claimed_by"] == "tyrone"
    with pytest.raises(board.BoardError, match="already claimed by tyrone"):
        board.claim(conn, task["id"], "minion")


def test_only_a_task_can_be_claimed(conn, alpha):
    msg = _post(conn, alpha, author="beepboop")
    with pytest.raises(board.BoardError):
        board.claim(conn, msg["id"], "tyrone")


def test_a_claimed_task_leaves_everyone_elses_inbox(conn, alpha):
    task = _post(conn, alpha, author="beepboop", kind="task")
    board.claim(conn, task["id"], "tyrone")
    assert board.inbox(conn, "minion") == []


def test_done_by_the_claimer_or_the_author(conn, alpha):
    t1 = _post(conn, alpha, author="beepboop", kind="task")
    board.claim(conn, t1["id"], "tyrone")
    assert board.done(conn, t1["id"], "tyrone")["status"] == "done"
    t2 = _post(conn, alpha, author="beepboop", kind="task")
    board.claim(conn, t2["id"], "tyrone")
    assert board.done(conn, t2["id"], "beepboop")["status"] == "done"


def test_done_by_a_bystander_is_refused(conn, alpha):
    task = _post(conn, alpha, author="beepboop", kind="task")
    board.claim(conn, task["id"], "tyrone")
    with pytest.raises(board.BoardError):
        board.done(conn, task["id"], "minion")


def test_a_done_task_cannot_be_claimed_or_finished_again(conn, alpha):
    task = _post(conn, alpha, author="beepboop", kind="task")
    board.done(conn, task["id"], "beepboop")
    with pytest.raises(board.BoardError):
        board.claim(conn, task["id"], "tyrone")
    with pytest.raises(board.BoardError):
        board.done(conn, task["id"], "beepboop")


def test_a_done_post_leaves_the_inbox_even_unread(conn, alpha):
    task = _post(conn, alpha, author="beepboop", kind="task")
    board.done(conn, task["id"], "beepboop")
    assert board.inbox(conn, "tyrone") == []


# --- the fence --------------------------------------------------------------

SCOPED = Grant("minion", {"alpha": WRITE, "gamma": READ})


def test_a_scoped_inbox_never_shows_another_namespace(conn, alpha, hidden):
    _post(conn, hidden, author="tyrone", body="SECRET")
    visible = _post(conn, alpha, author="tyrone")
    with grants.bind(SCOPED, conn):
        assert [p["id"] for p in board.inbox(conn, "minion")] == [visible["id"]]
        assert board.unread_count(conn, "minion") == 1


def test_a_scoped_thread_of_a_hidden_post_reads_as_missing(conn, hidden):
    secret = _post(conn, hidden, author="tyrone", body="SECRET")
    with grants.bind(SCOPED, conn), pytest.raises(board.BoardError) as exc:
        board.thread(conn, secret["id"], "minion")
    assert "SECRET" not in str(exc.value)
    assert board.unread_count(conn, "minion") == 1  # nothing was marked read


def test_posting_needs_write(conn):
    gamma = _ns(conn, "gamma")
    with grants.bind(SCOPED, conn), pytest.raises(grants.AccessDenied):
        _post(conn, gamma, author="minion")


def test_replying_into_a_read_only_namespace_is_refused(conn):
    gamma = _ns(conn, "gamma")
    root = _post(conn, gamma, author="tyrone")
    with grants.bind(SCOPED, conn), pytest.raises(grants.AccessDenied):
        _post(conn, gamma, author="minion", parent_id=root["id"])


def test_replying_to_a_hidden_post_reads_as_missing(conn, alpha, hidden):
    secret = _post(conn, hidden, author="tyrone")
    with grants.bind(SCOPED, conn), pytest.raises(board.BoardError):
        _post(conn, alpha, author="minion", parent_id=secret["id"])


def test_claiming_needs_write(conn):
    gamma = _ns(conn, "gamma")
    task = _post(conn, gamma, author="tyrone", kind="task")
    with grants.bind(SCOPED, conn), pytest.raises(grants.AccessDenied):
        board.claim(conn, task["id"], "minion")


# --- addressing -------------------------------------------------------------


def test_inbox_uri_is_per_persona():
    assert board.inbox_uri("beepboop") == "gingugu://board/inbox/beepboop"
    assert board.inbox_uri("*") == "gingugu://board/inbox/*"


def test_touched_inboxes_names_the_recipient_inbox(conn, alpha):
    p = _post(conn, alpha, author="tyrone", recipient="beepboop")
    assert board.touched_uris(p) == ["gingugu://board/inbox/beepboop"]
    b = _post(conn, alpha, author="tyrone")
    assert board.touched_uris(b) == ["gingugu://board/inbox/*"]
