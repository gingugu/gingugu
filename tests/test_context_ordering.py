"""Position of memories in the ``memory_context`` payload, over the live tool surface.

``build_context`` is well covered for *membership* (test_pinned.py) and asserts
position at the unit level, but every one of those assertions stops short of the
MCP handler. These tests assert POSITION in the payload the client actually
receives, which is the layer where a second sort can undo the selection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def _backdate(tmp_path, *memory_ids: str) -> None:
    """Push memories into the past, one day apart, over the tool surface's DB.

    These tests reach the server through MCP rather than the ``store`` fixture,
    so they cannot use the ``backdate`` fixture - but they need the same thing
    from it. Consecutive stores tie on a coarse clock (Windows resolved
    ``datetime.now()`` to 15.6ms before Python 3.13), and a test that asserts
    one memory is the newest write must supply that ordering rather than race
    for it.
    """
    conn = sqlite3.connect(tmp_path / "ordering.db")
    base = datetime.now(UTC) - timedelta(days=len(memory_ids) + 1)
    for offset, memory_id in enumerate(memory_ids):
        conn.execute(
            "UPDATE memories SET updated_at = ? WHERE id = ?",
            ((base + timedelta(days=offset)).isoformat(), memory_id),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "ordering.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "ship")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _store(server, title: str, *, namespace: str | None = None) -> str:
    args: dict = {
        "title": title,
        "content": f"body of {title}",
        "type": "preference",
        # The hint passes run their own searches, which would touch the
        # last_accessed values these tests order on.
        "dedupe_check": False,
        "relation_check": False,
    }
    if namespace is not None:
        args["namespace"] = namespace
    res = _payload(await server.call_tool("memory_store", args))
    assert res["ok"]
    return res["memory"]["id"]


async def _context(server, **kwargs) -> list[dict]:
    res = _payload(await server.call_tool("memory_context", kwargs))
    assert res["ok"], res
    return res["memories"]


@pytest.mark.asyncio
async def test_pin_is_served_first_not_last(server) -> None:
    """A pin carries no score; a score-ordered payload sinks it to the bottom."""
    pinned_id = await _store(server, "the constitution")
    for i in range(8):
        await _store(server, f"routine note {i}")
    res = _payload(
        await server.call_tool("memory_update", {"memory_id": pinned_id, "pinned": True})
    )
    assert res["ok"]

    memories = await _context(server, limit=5)

    ids = [m["id"] for m in memories]
    assert pinned_id in ids, "pin must surface at all"
    assert ids[0] == pinned_id, f"pin must lead the payload, was at index {ids.index(pinned_id)}"


@pytest.mark.asyncio
async def test_every_namespace_contributes_to_the_pin_tier(server) -> None:
    """Multi-namespace loads must not bury the second namespace's pins."""
    crow_pin = await _store(server, "who I am", namespace="crow")
    ship_pin = await _store(server, "how this repo works", namespace="ship")
    for i in range(6):
        await _store(server, f"crow filler {i}", namespace="crow")
        await _store(server, f"ship filler {i}", namespace="ship")
    for mid in (crow_pin, ship_pin):
        res = _payload(await server.call_tool("memory_update", {"memory_id": mid, "pinned": True}))
        assert res["ok"]

    memories = await _context(server, namespace="crow,ship", limit=5)

    ids = [m["id"] for m in memories]
    assert set(ids[:2]) == {crow_pin, ship_pin}, (
        f"both pins must lead the payload, got {ids[:2]} "
        f"(crow at {ids.index(crow_pin)}, ship at {ids.index(ship_pin)})"
    )


@pytest.mark.asyncio
async def test_freshest_memory_is_not_buried_by_its_placeholder_score(server, tmp_path) -> None:
    """The recency bucket's synthetic relevance must not decide presentation.

    The recency bucket is scored with a fixed ``relevance=0.5`` placeholder - it
    has no query to be relevant *to* - while task hits carry a real search
    relevance and heavily-read memories carry a high access component. Ordering
    the payload by that composite therefore ranks the freshest memory below the
    well-worn ones on a number that was never a relevance in the first place,
    which is how the "where we left off" anchor ends up in the tail of a long
    payload despite being handed a guaranteed slot.

    Task hits legitimately lead (the caller asked a question). What must hold is
    that the freshest memory sits in the guaranteed region right behind them,
    not below the score-ordered backfill.
    """
    kraken = [await _store(server, f"kraken sighting {i}") for i in range(8)]
    # Give the older memories a large access-count advantage, which is what
    # dominates the composite once the placeholder caps the fresh one.
    for _ in range(5):
        await server.call_tool("memory_recall", {"query": "kraken", "limit": 8})
    newest = await _store(server, "anchor stowed at dusk")
    # "Freshest" has to be a fact, not a hope: nine consecutive stores can all
    # land inside one tick of a coarse clock.
    _backdate(tmp_path, *kraken)

    memories = await _context(server, task_hint="kraken", limit=6)

    ids = [m["id"] for m in memories]
    assert newest in ids, "the freshest memory must survive the cut"
    kraken_positions = [i for i, m in enumerate(memories) if "kraken" in m["title"]]
    assert kraken_positions, "the task bucket must actually be exercised"
    # Guaranteed region = task quota (ceil(6*0.5)=3) + recency quota (ceil(6*0.3)=2).
    assert ids.index(newest) < 5, (
        f"freshest memory served at {ids.index(newest)} of {len(ids)}, "
        f"below the guaranteed region; order was {[m['title'] for m in memories]}"
    )


@pytest.mark.asyncio
async def test_second_namespace_is_interleaved_not_appended(server) -> None:
    """Concatenating namespaces buries the second one's best material.

    NOT a regression test: this passes against the pre-fix code too, because a
    global score sort also mixed the namespaces (badly, but it mixed them). It
    guards the *replacement* merge instead - preserving per-namespace order
    invites plain concatenation, and this is what makes that fail loudly.
    """
    for i in range(8):
        await _store(server, f"alpha note {i}", namespace="alpha")
    for i in range(8):
        await _store(server, f"beta note {i}", namespace="beta")

    memories = await _context(server, namespace="alpha,beta", limit=6)

    sources = [m["namespace"] for m in memories]
    assert "beta" in sources, "second namespace must surface at all"
    assert sources.index("beta") <= 1, (
        f"beta's first entry served at {sources.index('beta')} of {len(sources)}; "
        f"order was {sources}"
    )


@pytest.mark.asyncio
async def test_identical_timestamps_break_deterministically(server, tmp_path) -> None:
    """Ties on the bucket's native signal must not be left to SQLite.

    Memories written in one batch share a timestamp, and on a coarse system
    clock two separate writes land in the same tick - so `last_accessed` ties
    are routine, not exotic. Left untied, order comes from rowid on some
    platforms and from the clock on others: CI caught exactly that, passing on
    six runners and failing on two. The composite score breaks the tie, which is
    also what keeps the architecture/decision boost meaningful.
    """
    import sqlite3

    plain = await _store(server, "plain fact")
    arch_res = _payload(
        await server.call_tool(
            "memory_store",
            {
                "title": "arch note",
                "content": "body",
                "type": "architecture",
                "dedupe_check": False,
                "relation_check": False,
            },
        )
    )
    arch = arch_res["memory"]["id"]

    # Force the exact tie the coarse-clock runners produce. EVERY timestamp the
    # composite score reads must be pinned, not just last_accessed: leave
    # created_at alone and the two rows differ by microseconds of freshness,
    # which decides the order on its own and the boost is never exercised.
    conn = sqlite3.connect(tmp_path / "ordering.db")
    conn.execute(
        "UPDATE memories SET last_accessed = :t, created_at = :t, "
        "updated_at = :t, last_confirmed = :t",
        {"t": "2026-01-01T00:00:00+00:00"},
    )
    conn.commit()
    conn.close()

    memories = await _context(server, limit=5)

    ids = [m["id"] for m in memories]
    assert ids.index(arch) < ids.index(plain), (
        "architecture must win a last_accessed tie via the type boost; "
        f"order was {[m['title'] for m in memories]}"
    )


def _mem(mid: str, *, namespace_id: str, score: float | None, pinned: bool):
    from gingugu.models import Memory

    t = "2026-01-01T00:00:00+00:00"
    return Memory(
        id=mid,
        namespace_id=namespace_id,
        type="preference",
        title=f"memory {mid}",
        content="body",
        created_at=t,
        updated_at=t,
        last_accessed=t,
        pinned=pinned,
        score=score,
    )


def test_pin_survives_dedup_against_a_scored_duplicate() -> None:
    """A pin must be emitted as ITSELF, not traded for a scored duplicate.

    De-duplication keeps the highest-scoring instance of a memory, and a pin
    scores ``None``. On a multi-namespace load the same memory can reach a
    second namespace's cross-namespace bucket WITH a score, so the scored
    instance wins de-dup and the pin arrives carrying a score.

    That is not a position bug - the surrounding tests cover position, and it
    lands in the right place. It is an identity bug: scorelessness is how a
    caller knows a memory bypassed ranking, so a pin that arrives scored is
    indistinguishable from an ordinary ranked hit.

    Asserted at the merge rather than over the tool surface on purpose: which
    memories the cross-namespace bucket happens to reach is a ranking
    heuristic, and a test that depends on it would assert the contract only by
    luck.
    """
    from gingugu.handlers.recall import _merge_namespace_context

    pin = _mem("shared", namespace_id="crow", score=None, pinned=True)
    scored_duplicate = _mem("shared", namespace_id="crow", score=0.775, pinned=True)
    other = _mem("other", namespace_id="ship", score=0.9, pinned=False)

    # What the handler builds: the pin came from crow's pin tier, while ship's
    # ranked tail reached the same memory and scored it.
    best = {"shared": scored_duplicate, "other": other}
    out = _merge_namespace_context([pin], [[other, scored_duplicate]], best)

    emitted = {m.id: m for m in out}
    assert [m.id for m in out] == ["shared", "other"], "pin still leads, emitted once"
    assert emitted["shared"].score is None, (
        "a pin must arrive scoreless; de-dup traded it for the scored duplicate "
        f"(score={emitted['shared'].score})"
    )
    assert emitted["other"].score == 0.9, "ranked entries still take their best instance"
