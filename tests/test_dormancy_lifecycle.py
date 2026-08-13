"""End-to-end dormancy lifecycle: cross the threshold, then wake back up.

The unit tests around ``_spread_activation`` prove the wake *mechanism* works
when called directly with a seed list. They do not prove the lifecycle: that a
memory genuinely past ``DORMANT_AFTER_DAYS`` is counted as dormant, that an
ordinary ``memory_recall`` wakes it without anyone passing a seed by hand, and
that the dormancy accounting flips back.

That matters because the threshold is 90 days and no store in existence has
run long enough to cross it in production — the wake path has never executed
against a real dormant memory. These tests force the clock forward so the
behaviour is verified now rather than discovered later.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from gingugu.decay import DORMANT_AFTER_DAYS, is_dormant


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "dormancy.db"


@pytest.fixture
def server(db_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMORY_NAMESPACE", "dorm")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


def _backdate(db_path, memory_id: str, days: int) -> None:
    """Push a memory's clock into the past, out-of-band.

    Written directly rather than through the store because every public write
    path deliberately refreshes ``last_accessed`` — which is the exact reason
    this state cannot be reached from the tool surface.
    """
    old = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE memories SET last_accessed = ? WHERE id = ?", (old, memory_id))
    conn.commit()
    conn.close()


def _last_accessed(db_path, memory_id: str) -> str:
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT last_accessed FROM memories WHERE id = ?", (memory_id,)).fetchone()
    conn.close()
    return row[0]


async def _store(server, title: str, content: str) -> str:
    res = _payload(
        await server.call_tool("memory_store", {"title": title, "content": content, "type": "fact"})
    )
    assert res["ok"]
    return res["memory"]["id"]


def test_threshold_boundary_is_exact() -> None:
    now = datetime.now(UTC)
    just_inside = (now - timedelta(days=DORMANT_AFTER_DAYS - 1)).isoformat()
    just_past = (now - timedelta(days=DORMANT_AFTER_DAYS + 1)).isoformat()
    assert is_dormant(just_inside) is False
    assert is_dormant(just_past) is True


@pytest.mark.asyncio
async def test_a_memory_past_the_threshold_is_counted_dormant(server, db_path) -> None:
    mid = await _store(server, "old fact", "something learned long ago")
    assert _payload(await server.call_tool("memory_stats", {}))["stats"]["dormant_count"] == 0

    _backdate(db_path, mid, DORMANT_AFTER_DAYS + 10)

    stats = _payload(await server.call_tool("memory_stats", {}))["stats"]
    assert stats["dormant_count"] == 1


@pytest.mark.asyncio
async def test_recall_wakes_a_dormant_neighbour_end_to_end(server, db_path) -> None:
    """The headline never-forget claim, exercised the way a session would.

    No seed list is passed by hand: an ordinary recall for the *seed* has to
    reach through the relation and reset the dormant neighbour's clock.
    """
    seed = await _store(server, "postgres tuning", "shared_buffers and work_mem")
    neighbour = await _store(server, "the outage", "the night the connection pool ran dry")
    rel = _payload(
        await server.call_tool(
            "memory_relate",
            {"source_id": neighbour, "target_id": seed, "relation_type": "caused_by"},
        )
    )
    assert rel["ok"]

    _backdate(db_path, neighbour, DORMANT_AFTER_DAYS + 30)
    assert is_dormant(_last_accessed(db_path, neighbour)) is True
    assert _payload(await server.call_tool("memory_stats", {}))["stats"]["dormant_count"] == 1

    recalled = _payload(await server.call_tool("memory_recall", {"query": "postgres tuning"}))
    assert recalled["ok"]

    assert is_dormant(_last_accessed(db_path, neighbour)) is False
    assert _payload(await server.call_tool("memory_stats", {}))["stats"]["dormant_count"] == 0


@pytest.mark.asyncio
async def test_waking_a_dormant_memory_does_not_inflate_its_access_count(server, db_path) -> None:
    """Reactivation is not a read. Dormancy must not become a ranking cheat code.

    The two memories share no vocabulary on purpose: the neighbour must be
    reachable *only* through the relation, or a direct search hit would credit
    a genuine access and the assertion would pass for the wrong reason.
    """
    seed = await _store(server, "kubernetes ingress", "nginx controller annotations")
    neighbour = await _store(server, "payroll spreadsheet", "quarterly bonus calculations")
    await server.call_tool(
        "memory_relate",
        {"source_id": neighbour, "target_id": seed, "relation_type": "caused_by"},
    )
    _backdate(db_path, neighbour, DORMANT_AFTER_DAYS + 5)

    await server.call_tool("memory_recall", {"query": "kubernetes ingress"})

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT access_count FROM memories WHERE id = ?", (neighbour,)).fetchone()[
        0
    ]
    conn.close()
    assert count == 0


@pytest.mark.asyncio
async def test_dormancy_never_deletes_or_demotes(server, db_path) -> None:
    """Dormant is resting, not rotting: no deletion, no confidence change."""
    mid = await _store(server, "ancient but true", "still valid, just untouched")
    await server.call_tool("memory_update", {"memory_id": mid, "confidence": "verified"})
    _backdate(db_path, mid, DORMANT_AFTER_DAYS * 4)

    await server.call_tool("memory_stats", {})

    found = _payload(await server.call_tool("memory_recall", {"query": "ancient but true"}))
    ids = {m["id"] for m in found["memories"]}
    assert mid in ids, "a dormant memory must remain fully retrievable"
    assert next(m for m in found["memories"] if m["id"] == mid)["confidence"] == "verified"


@pytest.mark.asyncio
async def test_context_load_refreshes_the_dormancy_clock(server, db_path) -> None:
    """Documents a real consequence: frequently surfaced memories never go dormant.

    ``memory_context`` refreshes the dormancy clock on everything it surfaces
    (deliberately, without crediting an access). Because the session-start
    protocol calls it every session, anything it routinely surfaces can never
    accumulate ``DORMANT_AFTER_DAYS`` untouched — dormancy only ever reaches
    the tail. That is intended, but it is load-bearing and was undocumented,
    so it is pinned here: if this ever flips, dormancy starts firing on hot
    memories and the never-forget model changes shape.
    """
    mid = await _store(server, "surfaced often", "the kind of thing context always loads")
    _backdate(db_path, mid, DORMANT_AFTER_DAYS + 15)
    assert is_dormant(_last_accessed(db_path, mid)) is True

    ctx = _payload(await server.call_tool("memory_context", {"limit": 10}))
    assert mid in {m["id"] for m in ctx["memories"]}

    assert is_dormant(_last_accessed(db_path, mid)) is False
