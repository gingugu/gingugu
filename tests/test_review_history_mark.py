"""Board row 9 / item 20: a deliberate reviewed-as-history mark for review hints.

Two classes of gated hint have no permanent reconciliation: a wait narrated
with its outcome ("awaiting the captain's merge. (It merged 2026-07-07.)"), and
refs the claims ledger cannot hold (a persona namespace with no default repo).
Re-confirming only snoozes them for REVIEW_HINT_AFTER_DAYS.

``memory_update(review="history")`` records that a reviewer read the memory and
judged it accurate history. The mark lives in ``metadata.review``, is bound to
the content it was set on (a content edit voids it), silences GATED signals
only - an expired or as-of date is a date and still fires - and is visible on
every read surface and counted by ``memory_stats`` so it can be audited.
``review=""`` clears it. It is never inferred from type or tags.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from gingugu.staleness import REVIEW_HINT_AFTER_DAYS, history_mark, review_signals

_OLD = (datetime.now(UTC) - timedelta(days=REVIEW_HINT_AFTER_DAYS + 30)).isoformat()
NARRATED = "Opened the fix. Awaiting Mr. Boomtastic's merge. (It merged 2026-07-07.)"
EXPIRED = "Token rotation note. Awaiting Mr. Boomtastic's merge. Expires 2020-01-01."


# --- 1. the pure function -----------------------------------------------------


def test_a_history_mark_silences_gated_signals() -> None:
    kw = dict(memory_type="workflow", last_confirmed=_OLD, updated_at=_OLD, created_at=_OLD)
    assert "waiting-on" in review_signals(NARRATED, **kw)
    assert review_signals(NARRATED, reviewed_history=True, **kw) == []


def test_a_history_mark_leaves_ungated_dates_alone() -> None:
    kw = dict(memory_type="workflow", last_confirmed=_OLD, updated_at=_OLD, created_at=_OLD)
    assert review_signals(EXPIRED, reviewed_history=True, **kw) == ["expired-date"]


def test_history_mark_is_bound_to_the_content_it_was_set_on() -> None:
    mark = {"review": {"history": "2026-11-26", "by": "beepboop"}}
    assert history_mark(json.dumps(mark), NARRATED) is None  # no content binding: not a mark
    assert history_mark(None, NARRATED) is None
    assert history_mark("not json", NARRATED) is None
    assert history_mark(json.dumps({"review": "history"}), NARRATED) is None


# --- 2. the tool surface ------------------------------------------------------


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    db = tmp_path / "mark.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    monkeypatch.setenv("MEMORY_PERSONA", "beepboop")
    from gingugu.server import build_server

    srv = build_server()
    srv._test_db = db  # type: ignore[attr-defined]
    return srv


def _age(server, memory_id: str) -> None:
    conn = sqlite3.connect(server._test_db)
    conn.execute(
        "UPDATE memories SET created_at=?, updated_at=?, last_confirmed=? WHERE id=?",
        (_OLD, _OLD, _OLD, memory_id),
    )
    conn.commit()
    conn.close()


async def _store(server, content: str) -> str:
    out = _payload(
        await server.call_tool(
            "memory_store", {"title": "handoff", "content": content, "type": "workflow"}
        )
    )
    _age(server, out["memory"]["id"])
    return out["memory"]["id"]


async def _update(server, mid: str, **kw) -> dict:
    return _payload(await server.call_tool("memory_update", {"memory_id": mid, **kw}))


async def _read(server, mid: str) -> dict:
    return _payload(await server.call_tool("memory_search", {"ids": mid}))["memories"][0]


async def _review(server) -> dict:
    return _payload(await server.call_tool("memory_stats", {"review_limit": 100}))["stats"][
        "review"
    ]


@pytest.mark.asyncio
async def test_the_mark_silences_reads_and_stats(server) -> None:
    mid = await _store(server, NARRATED)
    assert "waiting-on" in (await _read(server, mid)).get("review_hints", [])
    assert mid in {r["id"] for r in (await _review(server))["sample"]}

    out = await _update(server, mid, review="history")
    assert out["ok"], out
    _age(server, mid)  # past the snooze: only the mark can explain silence now

    mem = await _read(server, mid)
    assert "review_hints" not in mem
    assert mem["reviewed"] == {"as": "history", "on": datetime.now(UTC).date().isoformat(),
                               "by": "beepboop"}
    review = await _review(server)
    assert mid not in {r["id"] for r in review["sample"]}
    assert review["reviewed_as_history"] == 1


@pytest.mark.asyncio
async def test_the_mark_shows_on_context_too(server) -> None:
    mid = await _store(server, NARRATED)
    await _update(server, mid, review="history")
    _age(server, mid)
    out = _payload(await server.call_tool("memory_context", {"task_hint": "merge handoff"}))
    hit = next(m for m in out["memories"] if m["id"] == mid)
    assert hit["reviewed"]["as"] == "history"
    assert "review_hints" not in hit


@pytest.mark.asyncio
async def test_an_expired_date_still_fires_under_the_mark(server) -> None:
    mid = await _store(server, EXPIRED)
    await _update(server, mid, review="history")
    _age(server, mid)
    assert (await _read(server, mid))["review_hints"] == ["expired-date"]


@pytest.mark.asyncio
async def test_a_content_edit_voids_the_mark(server) -> None:
    mid = await _store(server, NARRATED)
    await _update(server, mid, review="history")
    await _update(server, mid, content=NARRATED + " Then awaiting Joe's sign-off.")
    _age(server, mid)
    mem = await _read(server, mid)
    assert "waiting-on" in mem["review_hints"]
    assert "reviewed" not in mem
    assert (await _review(server))["reviewed_as_history"] == 0


@pytest.mark.asyncio
async def test_clearing_the_mark_brings_the_hints_back(server) -> None:
    mid = await _store(server, NARRATED)
    await _update(server, mid, review="history")
    out = await _update(server, mid, review="")
    assert out["ok"], out
    _age(server, mid)
    mem = await _read(server, mid)
    assert "waiting-on" in mem["review_hints"]
    assert "reviewed" not in mem


@pytest.mark.asyncio
async def test_the_mark_keeps_other_metadata_and_does_not_confirm(server) -> None:
    mid = await _store(server, NARRATED)
    await _update(server, mid, metadata={"source_pr": 7})
    _age(server, mid)
    out = await _update(server, mid, review="history")
    meta = out["memory"]["metadata"]
    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert meta["source_pr"] == 7
    assert out["memory"]["last_confirmed"] == _OLD  # a mark is not a re-check of the claim


@pytest.mark.asyncio
async def test_an_unknown_review_value_is_refused_and_changes_nothing(server) -> None:
    mid = await _store(server, NARRATED)
    out = await _update(server, mid, review="timeless")
    assert out["ok"] is False
    assert "history" in out["error"]
    assert "reviewed" not in await _read(server, mid)


@pytest.mark.asyncio
async def test_a_hand_written_review_block_is_not_a_mark(server) -> None:
    out = _payload(
        await server.call_tool(
            "memory_store",
            {
                "title": "handoff",
                "content": NARRATED,
                "type": "workflow",
                "metadata": {"review": {"history": "2026-11-26", "by": "someone"}},
            },
        )
    )
    mid = out["memory"]["id"]
    _age(server, mid)
    assert "waiting-on" in (await _read(server, mid))["review_hints"]
