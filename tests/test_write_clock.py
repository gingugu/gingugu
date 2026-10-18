"""``updated_at`` is a write clock: it moves when the record's own fields do.

Two readers trust it. The ``memory_context`` recency bucket orders by it (the
"where we left off" signal), and ``decay.reference_timestamp`` takes the latest
of it, ``last_confirmed`` and ``created_at`` as the freshness anchor for
scoring and staleness. So a write that changes nothing about the memory -
resolving a claim, replacing tags, pinning - must leave it alone, or
reconciling an August note puts it back in front of the next session start and
makes it look freshly checked.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

import pytest

from gingugu.context_buckets import recently_active
from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


def _two(store: MemoryStore, namespaces: NamespaceManager, backdate: Callable[..., None]):
    ns_id = namespaces.get_or_create("test-ns").id
    old = store.create(namespace_id=ns_id, type=MemoryType.WORKFLOW, title="old", content="a")
    new = store.create(namespace_id=ns_id, type=MemoryType.WORKFLOW, title="new", content="b")
    backdate(old.id, new.id)
    return ns_id, store.get(old.id, record_access=False), new


def test_a_no_op_update_leaves_the_clock(store, namespaces, backdate) -> None:
    _, old, _ = _two(store, namespaces, backdate)
    after = store.update(old.id)
    assert after.updated_at == old.updated_at


def test_restating_the_same_values_leaves_the_clock(store, namespaces, backdate) -> None:
    _, old, _ = _two(store, namespaces, backdate)
    after = store.update(old.id, title=old.title, content=old.content, type=old.type)
    assert after.updated_at == old.updated_at


def test_pinning_leaves_the_clock(store, namespaces, backdate) -> None:
    ns_id, old, new = _two(store, namespaces, backdate)
    store.update(old.id, pinned=True)
    store.update(old.id, pinned=False)
    assert store.get(old.id, record_access=False).updated_at == old.updated_at
    assert [m.id for m in recently_active(store.conn, ns_id, limit=10)] == [new.id, old.id]


@pytest.mark.parametrize(
    "change",
    [
        {"title": "renamed"},
        {"content": "rewritten"},
        {"type": MemoryType.FACT},
        {"confidence": Confidence.VERIFIED},
        {"metadata": '{"k": 1}'},
        {"provenance": "measured"},
        {"about": "what it is for"},
    ],
)
def test_a_real_field_change_moves_the_clock(store, namespaces, backdate, change) -> None:
    ns_id, old, new = _two(store, namespaces, backdate)
    after = store.update(old.id, **change)
    assert after.updated_at > old.updated_at
    assert [m.id for m in recently_active(store.conn, ns_id, limit=10)][0] == old.id


# --- through the tool surface: the writes that motivated this -----------------


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "clock.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    from gingugu.server import build_server

    return build_server(), tmp_path / "clock.db"


def _stamp(db_path, memory_id: str, ts: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (ts, memory_id))
    conn.commit()
    conn.close()


def _clock(db_path, memory_id: str) -> str:
    conn = sqlite3.connect(db_path)
    (ts,) = conn.execute("SELECT updated_at FROM memories WHERE id = ?", (memory_id,)).fetchone()
    conn.close()
    return ts


_PAST = "2026-08-01T00:00:00+00:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"resolve_claims": "gingugu#49"},
        {"resolve_claims": "all"},
        {"tags": "resume,merged"},
        {"pinned": True},
    ],
)
async def test_bookkeeping_through_memory_update_leaves_the_clock(server, args) -> None:
    srv, db_path = server
    stored = _payload(
        await srv.call_tool(
            "memory_store",
            {"title": "resume", "content": "PR #49 open, waiting on review", "type": "workflow"},
        )
    )
    mid = stored["memory"]["id"]
    _stamp(db_path, mid, _PAST)
    out = _payload(await srv.call_tool("memory_update", {"memory_id": mid, **args}))
    assert out["ok"] is True
    assert _clock(db_path, mid) == _PAST


@pytest.mark.asyncio
async def test_a_content_edit_through_memory_update_moves_the_clock(server) -> None:
    srv, db_path = server
    stored = _payload(
        await srv.call_tool(
            "memory_store", {"title": "resume", "content": "PR #49 open", "type": "workflow"}
        )
    )
    mid = stored["memory"]["id"]
    _stamp(db_path, mid, _PAST)
    await srv.call_tool("memory_update", {"memory_id": mid, "content": "PR #49 merged"})
    assert _clock(db_path, mid) > _PAST
