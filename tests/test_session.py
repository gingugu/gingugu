"""Tests for per-session identity on ``access_log`` rows.

The point of the column is co-access: "which memories are retrieved together".
That only works if one call's rows share a key and different sessions get
different keys, so those two properties are what these assert - plus the
negative one that matters most, that an unknown session is stored as NULL
rather than as a shared placeholder that would fake co-access.
"""

from __future__ import annotations

import pytest

from gingugu import session as session_mod
from gingugu.models import MemoryType


class _FakeSession:
    """Stands in for ``mcp.server.session.ServerSession`` (weak-referenceable)."""


class _FakeRequestContext:
    def __init__(self, session: object) -> None:
        self.session = session


@pytest.fixture
def in_request(monkeypatch):
    """Enter a fake MCP request context, and hand back a setter to swap sessions."""
    from mcp.server.lowlevel.server import request_ctx

    tokens = []

    def enter(sess: object) -> None:
        tokens.append(request_ctx.set(_FakeRequestContext(sess)))

    yield enter
    for token in reversed(tokens):
        request_ctx.reset(token)


@pytest.fixture
def seeded(store, namespaces):
    ns = namespaces.get_or_create("session-ns")
    a = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="a", content="a")
    b = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="b", content="b")
    return a, b


def _contexts(db) -> list[str | None]:
    return [r[0] for r in db.conn.execute("SELECT context FROM access_log").fetchall()]


# --- current_session_id ------------------------------------------------------


def test_returns_none_outside_a_request():
    assert session_mod.current_session_id() is None


def test_same_session_yields_a_stable_id(in_request):
    sess = _FakeSession()
    in_request(sess)
    first = session_mod.current_session_id()
    second = session_mod.current_session_id()
    assert first is not None
    assert first == second


def test_distinct_sessions_yield_distinct_ids(in_request):
    in_request(_FakeSession())
    first = session_mod.current_session_id()
    in_request(_FakeSession())
    second = session_mod.current_session_id()
    assert first is not None and second is not None
    assert first != second


def test_non_weak_referenceable_session_degrades_to_none(in_request):
    class _Slotted:
        __slots__ = ()

    in_request(_Slotted())
    assert session_mod.current_session_id() is None


def test_request_context_without_a_session_degrades_to_none(in_request, monkeypatch):
    from mcp.server.lowlevel.server import request_ctx

    class _NoSession:
        pass

    token = request_ctx.set(_NoSession())
    try:
        assert session_mod.current_session_id() is None
    finally:
        request_ctx.reset(token)


# --- record_accesses ---------------------------------------------------------


def test_access_rows_are_null_outside_a_request(db, store, seeded):
    a, b = seeded
    store.record_accesses([a.id, b.id])
    assert _contexts(db) == [None, None]


def test_one_call_stamps_every_row_with_one_session(db, store, seeded, in_request):
    a, b = seeded
    in_request(_FakeSession())
    store.record_accesses([a.id, b.id])

    contexts = _contexts(db)
    assert len(contexts) == 2
    assert contexts[0] is not None
    assert contexts[0] == contexts[1], "co-access needs one call to share a key"


def test_separate_sessions_do_not_share_a_key(db, store, seeded, in_request):
    a, b = seeded

    in_request(_FakeSession())
    store.record_accesses([a.id])
    in_request(_FakeSession())
    store.record_accesses([b.id])

    first, second = _contexts(db)
    assert first is not None and second is not None
    assert first != second


def test_repeated_calls_in_one_session_share_a_key(db, store, seeded, in_request):
    a, b = seeded
    in_request(_FakeSession())
    store.record_accesses([a.id])
    store.record_accesses([b.id])

    first, second = _contexts(db)
    assert first is not None
    assert first == second, "co-access spans a whole session, not one call"


def test_access_count_and_timestamps_still_recorded(db, store, seeded, in_request):
    a, _ = seeded
    in_request(_FakeSession())
    assert store.record_accesses([a.id]) == 1

    row = db.conn.execute(
        "SELECT access_count, last_accessed FROM memories WHERE id = ?", (a.id,)
    ).fetchone()
    assert row[0] == 1
    assert row[1] is not None


def test_touch_many_still_writes_no_access_row(db, store, seeded, in_request):
    a, _ = seeded
    in_request(_FakeSession())
    store.touch_many([a.id])
    assert _contexts(db) == []
