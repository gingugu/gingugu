"""Tests for staleness review hints: pure detection + tool-surface wiring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gingugu.staleness import REVIEW_HINT_AFTER_DAYS, review_signals

_NOW = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
_OLD = (_NOW - timedelta(days=REVIEW_HINT_AFTER_DAYS + 7)).isoformat()
_FRESH = (_NOW - timedelta(days=1)).isoformat()


def _signals(content: str, *, confirmed: str = _OLD) -> list[str]:
    return review_signals(content, last_confirmed=confirmed, now=_NOW)


# --- gated signals ----------------------------------------------------------


def test_open_pr_reference_fires_when_stale() -> None:
    assert "open-pr-reference" in _signals("PR #947 is still open, needs Joe to merge")


def test_open_pr_reference_reverse_order() -> None:
    assert "open-pr-reference" in _signals("waiting on PR #955 before the flip")


def test_merge_request_spelling() -> None:
    assert "open-pr-reference" in _signals("MR !6 pending review")


def test_waiting_on_phrasing() -> None:
    assert "waiting-on" in _signals("blocked on the security signoff")
    assert "waiting-on" in _signals("Waiting for Baskar to approve")


def test_unmerged_branch() -> None:
    assert "unmerged-branch" in _signals("branch feature/foo is not yet merged")


def test_gated_signals_stay_quiet_when_recently_confirmed() -> None:
    assert _signals("PR #947 is still open, waiting on Joe", confirmed=_FRESH) == []


def test_clean_content_has_no_signals() -> None:
    assert _signals("use WAL mode for SQLite; busy_timeout avoids SQLITE_BUSY") == []


def test_merged_pr_record_is_not_flagged() -> None:
    # Completed point-in-time records are fine — only in-flight phrasing trips.
    assert _signals("PR #955 merged Jun 16 2026, clusters restored") == []


# --- ungated signals (carry their own clock) --------------------------------


def test_expired_date_fires_even_when_fresh() -> None:
    sig = review_signals(
        "RollCall key expires 2026-06-29, rotate before then",
        last_confirmed=_FRESH,
        now=_NOW,
    )
    assert "expired-date" in sig


def test_future_expiry_does_not_fire() -> None:
    assert review_signals("cert expires 2027-01-01", last_confirmed=_FRESH, now=_NOW) == []


def test_old_as_of_date_fires() -> None:
    sig = review_signals("as of 2026-06-01 there are two replicas", last_confirmed=_FRESH, now=_NOW)
    assert "stale-as-of-date" in sig


def test_recent_as_of_date_does_not_fire() -> None:
    assert (
        review_signals("as of 2026-07-06 there are two replicas", last_confirmed=_FRESH, now=_NOW)
        == []
    )


def test_anchor_falls_back_to_created_at() -> None:
    sig = review_signals("waiting on the vendor", created_at=_OLD, now=_NOW)
    assert "waiting-on" in sig


# --- tool-surface wiring -----------------------------------------------------


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "review.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "review-ns")
    from gingugu.server import build_server

    return build_server()


@pytest.mark.asyncio
async def test_context_and_stats_surface_review_hints(server) -> None:
    """An expired-date memory (ungated, so it trips immediately after store)
    must carry review_hints in memory_context and count in the stats sweep;
    a clean memory must not."""
    flagged = _payload(
        await server.call_tool(
            "memory_store",
            {
                "content": "the API key expires 2026-06-29, rotate it",
                "title": "key expiry",
                "type": "fact",
            },
        )
    )
    clean = _payload(
        await server.call_tool(
            "memory_store",
            {"content": "use WAL mode for sqlite", "title": "wal", "type": "pattern"},
        )
    )
    assert flagged["ok"] and clean["ok"]

    ctx = _payload(await server.call_tool("memory_context", {}))
    by_id = {m["id"]: m for m in ctx["memories"]}
    assert "review_hints" in by_id[flagged["memory"]["id"]]
    assert "expired-date" in by_id[flagged["memory"]["id"]]["review_hints"]
    assert "review_hints" not in by_id[clean["memory"]["id"]]

    stats = _payload(await server.call_tool("memory_stats", {}))
    review = stats["stats"]["review"]
    assert review["review_suggested"] == 1
    assert review["sample"][0]["id"] == flagged["memory"]["id"]
    assert "expired-date" in review["sample"][0]["signals"]
