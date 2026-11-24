"""Review-hint precision: two false-positive classes measured on the live brain.

1. ``waiting-on`` looked for a named agent in a fixed window that ran past the
   end of the sentence, so the next sentence's opening word ("Had", "Then",
   "Use") read as the person being waited on.
2. ``open-pr-reference`` fired on prose "PR #47 open" even when the claims
   ledger already records #47 as settled, so accurate history re-flagged every
   14 days forever.

Examples are lifted from memories that flagged on 2026-11-24.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from gingugu.staleness import REVIEW_HINT_AFTER_DAYS, review_signals

_OLD = (datetime.now(UTC) - timedelta(days=REVIEW_HINT_AFTER_DAYS + 30)).isoformat()


def _signals(content: str, settled: frozenset[int] | None = None) -> list[str]:
    return review_signals(
        content,
        memory_type="workflow",
        last_confirmed=_OLD,
        updated_at=_OLD,
        created_at=_OLD,
        settled_refs=settled,
    )


# --- 1. waiting-on stops at the end of the sentence -------------------------


@pytest.mark.parametrize(
    "content",
    [
        "Dead code waiting for a tool. Had I estimated from memory I would have",
        'the "state the work as the work" feedback were all waiting for me. Then: verify',
        "A chained sleep is BLOCKED by the harness. Use a background call.",
        "refs sweep, blocked by item 5. Then\n  `_qualify` reads a repo",
        "waiting for the next model registration.** Nothing about it",
    ],
)
def test_waiting_on_ignores_the_next_sentences_opening_word(content: str) -> None:
    assert "waiting-on" not in _signals(content)


@pytest.mark.parametrize(
    "content",
    [
        "PR #947 still open, waiting on Joe to review it.",
        "Complete and verified, AWAITING Mr. Boomtastic's go to commit.",
        "Blocked on Dr. Alvarez signing the form.",
        # A hard-wrapped line is still one sentence.
        "the whole sail ran inside a block, still awaiting\ncode-owner review from Baskar",
    ],
)
def test_waiting_on_still_fires_on_a_named_agent_in_the_same_sentence(content: str) -> None:
    assert "waiting-on" in _signals(content)


def test_waiting_on_does_not_cross_a_blank_line() -> None:
    assert "waiting-on" not in _signals("still waiting for it\n\nBrian said later")


# --- 2. settled PR refs do not re-flag --------------------------------------


def test_open_pr_reference_quiet_when_every_ref_is_settled() -> None:
    assert _signals("PR #47 is OPEN, awaiting review.", settled=frozenset({47})) == []


def test_open_pr_reference_fires_when_any_ref_is_unsettled() -> None:
    content = "PR #47 open. PR #48 open and unmerged."
    assert "open-pr-reference" in _signals(content, settled=frozenset({47}))


def test_open_pr_reference_unchanged_without_settled_refs() -> None:
    assert "open-pr-reference" in _signals("PR #47 is OPEN.")
    assert "open-pr-reference" in _signals("PR #47 is OPEN.", settled=frozenset())


def test_settled_pr_is_not_a_named_agent_for_waiting_on() -> None:
    content = "Release held, awaiting PR #61 to land."
    assert "waiting-on" in _signals(content)
    assert "waiting-on" not in _signals(content, settled=frozenset({61}))


def test_settled_refs_do_not_touch_ungated_signals() -> None:
    # Expired after the last confirmation, so the ungated clock is live.
    expired = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
    content = f"PR #47 open; token expires {expired}"
    assert _signals(content, settled=frozenset({47})) == ["expired-date"]


# --- 2b. the tool surface feeds the ledger through --------------------------


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    db = tmp_path / "precision.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
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


async def _flagged_ids(server) -> set[str]:
    out = _payload(await server.call_tool("memory_stats", {"review_limit": 100}))
    return {row["id"] for row in out["stats"]["review"]["sample"]}


async def _hints(server, memory_id: str) -> list[str]:
    out = _payload(await server.call_tool("memory_search", {"ids": memory_id}))
    return out["memories"][0].get("review_hints", [])


@pytest.mark.asyncio
async def test_resolved_claim_clears_the_hint_on_stats_and_reads(server) -> None:
    stored = _payload(
        await server.call_tool(
            "memory_store",
            {"title": "handoff", "content": "PR #10 is OPEN, awaiting review.", "type": "workflow"},
        )
    )
    mid = stored["memory"]["id"]
    _age(server, mid)
    assert mid in await _flagged_ids(server)
    assert "open-pr-reference" in await _hints(server, mid)

    out = _payload(
        await server.call_tool("memory_update", {"memory_id": mid, "resolve_claims": "all"})
    )
    assert out["ok"]
    _age(server, mid)  # past the snooze: only the ledger can explain silence now

    assert mid not in await _flagged_ids(server)
    assert "open-pr-reference" not in await _hints(server, mid)


@pytest.mark.asyncio
async def test_unresolved_claim_still_flags(server) -> None:
    stored = _payload(
        await server.call_tool(
            "memory_store",
            {"title": "handoff", "content": "PR #11 is OPEN, awaiting review.", "type": "workflow"},
        )
    )
    mid = stored["memory"]["id"]
    _age(server, mid)
    assert mid in await _flagged_ids(server)


# --- 3. a PR reference needs its sigil -------------------------------------
# Same rule claims.py adopted in #65: "PR 1" is a plan ordinal, "pr 140" a
# column count. Examples from memories that flagged on 2026-11-24.


@pytest.mark.parametrize(
    "content",
    [
        "151 rows - state resolved 121, open 30; kind pr 140, mr 11. Matches",
        "Possibly makes the claude/channel adapter (PR2, needs a dev flag) optional",
        "Plan: PR 1 adds the table, PR 2 is still open for discussion",
    ],
)
def test_pr_without_sigil_is_not_a_reference(content: str) -> None:
    assert "open-pr-reference" not in _signals(content)


def test_pr_with_sigil_still_flags() -> None:
    assert "open-pr-reference" in _signals("PR #12 still open, needs review")
    assert "open-pr-reference" in _signals("MR !12 still open, needs review")


def test_pr_without_sigil_is_not_a_named_agent() -> None:
    assert "waiting-on" not in _signals("Release waiting on PR 2 from the plan list.")
    assert "waiting-on" in _signals("Release waiting on PR #2 to land.")


# --- 4. a wiki-link is a citation, not an assertion ------------------------


@pytest.mark.parametrize(
    "content",
    [
        "the state came from the linked title [[DESI-52 guardrails: PR #155 OPEN, merge HELD]].",
        "Same shape as\n[[PRODUCT GAP (FIXED in PR #47): open claims were NOT enumerable]]: a gap",
        "Follows [[RESUME (5th sail): PR #48 merged]] - the release decision pending it flagged",
    ],
)
def test_wiki_link_text_does_not_signal(content: str) -> None:
    assert _signals(content) == []


def test_dates_inside_a_wiki_link_do_not_signal() -> None:
    expired = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
    assert _signals(f"token expires {expired}") == ["expired-date"]
    assert _signals(f"See [[Rotation note: token expires {expired}]] for history.") == []


def test_text_outside_a_wiki_link_still_signals() -> None:
    content = "See [[old handoff]]. PR #12 is still open, waiting on Joe to review."
    assert "open-pr-reference" in _signals(content)
    assert "waiting-on" in _signals(content)
