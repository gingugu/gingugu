"""Who a board reply is addressed to when the caller does not say."""

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
def alpha(conn):
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (ns_id, "alpha", now, now),
    )
    return ns_id


def _post(conn, ns_id, author, **kw):
    kw.setdefault("body", "ahoy")
    return board.post(conn, namespace_id=ns_id, author=author, **kw)


def test_replying_to_someone_else_goes_to_them(conn, alpha):
    root = _post(conn, alpha, "tyrone", recipient="beepboop")
    assert _post(conn, alpha, "beepboop", parent_id=root["id"])["recipient"] == "tyrone"


def test_replying_to_my_own_root_goes_to_whoever_answered_it(conn, alpha):
    root = _post(conn, alpha, "beepboop", recipient="tyrone")
    _post(conn, alpha, "tyrone", parent_id=root["id"])
    ack = _post(conn, alpha, "beepboop", parent_id=root["id"])
    assert ack["recipient"] == "tyrone"


def test_replying_to_my_own_reply_goes_to_the_latest_other_voice(conn, alpha):
    root = _post(conn, alpha, "tyrone", recipient="*")
    _post(conn, alpha, "gemini", parent_id=root["id"])
    mine = _post(conn, alpha, "beepboop", parent_id=root["id"])
    follow_up = _post(conn, alpha, "beepboop", parent_id=mine["id"])
    assert follow_up["recipient"] == "gemini"


def test_replying_to_my_own_unanswered_root_goes_to_its_recipient(conn, alpha):
    root = _post(conn, alpha, "beepboop", recipient="tyrone")
    nudge = _post(conn, alpha, "beepboop", parent_id=root["id"])
    assert nudge["recipient"] == "tyrone"


def test_an_explicit_to_still_wins_on_my_own_thread(conn, alpha):
    root = _post(conn, alpha, "beepboop", recipient="tyrone")
    _post(conn, alpha, "tyrone", parent_id=root["id"])
    aside = _post(conn, alpha, "beepboop", parent_id=root["id"], recipient="gemini")
    assert aside["recipient"] == "gemini"
