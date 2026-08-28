"""Tests for staleness review hints: pure detection + tool-surface wiring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gingugu.staleness import REVIEW_HINT_AFTER_DAYS, review_signals

_NOW = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
_OLD = (_NOW - timedelta(days=REVIEW_HINT_AFTER_DAYS + 7)).isoformat()
_FRESH = (_NOW - timedelta(days=1)).isoformat()
# Confirmed before the 2026-06-29 expiry the date-signal tests use, so the
# "already reconciled" suppression does not apply.
_BEFORE_EXPIRY = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC).isoformat()


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


def test_pr_inside_longer_word_does_not_match() -> None:
    # Peer-review regression: "GDPR #5 open ..." must not trip the PR pattern.
    assert _signals("GDPR #5 open question about data retention") == []


def test_past_tense_blocked_on_is_not_flagged() -> None:
    # Peer-review regression: resolved narrative is history, not in-flight state.
    assert _signals("fixed the deadlock: worker was blocked on the queue lock") == []
    assert _signals("we were waiting for the vendor back then") == []


# --- ungated signals (carry their own clock) --------------------------------


def test_expired_date_fires_when_not_reconfirmed_since() -> None:
    # Confirmed BEFORE the expiry passed — nobody has looked at it since.
    sig = review_signals(
        "RollCall key expires 2026-06-29, rotate before then",
        last_confirmed=_BEFORE_EXPIRY,
        now=_NOW,
    )
    assert "expired-date" in sig


def test_expired_date_suppressed_when_reconfirmed_after_the_date() -> None:
    """Real-corpus regression: "RollCall key - EXPIRED unused, no renewal".

    The memory names 2026-06-29 and was reconfirmed 2026-07-20 with the
    resolved outcome written into the body. Re-flagging it forever asks the
    caller to reconcile something already reconciled.
    """
    sig = review_signals(
        "Decision: let it expire 2026-06-29 rather than renew. OUTCOME: it did.",
        last_confirmed=_FRESH,
        now=_NOW,
    )
    assert "expired-date" not in sig


def test_same_day_confirmation_does_not_suppress_its_own_date() -> None:
    """A memory written on day X saying "as of day X" must still flag later.

    A bare YYYY-MM-DD parses to midnight, so comparing a same-day confirmation
    directly against it would make every such memory silence itself at birth.
    """
    same_day = datetime(2026, 6, 1, 18, 30, tzinfo=UTC).isoformat()
    sig = review_signals(
        "as of 2026-06-01 there are two replicas", last_confirmed=same_day, now=_NOW
    )
    assert "stale-as-of-date" in sig


def test_quoted_expiry_does_not_fire() -> None:
    """A bug report *citing* an expiry is describing the phrase, not claiming it."""
    sig = review_signals(
        'the detector wrongly flags memories that quote "expire 2026-06-29" in prose',
        last_confirmed=_BEFORE_EXPIRY,
        now=_NOW,
    )
    assert "expired-date" not in sig


def test_future_expiry_does_not_fire() -> None:
    assert review_signals("cert expires 2027-01-01", last_confirmed=_FRESH, now=_NOW) == []


def test_expiry_today_is_still_valid() -> None:
    # Peer-review regression: an expiry counts only once its whole day passed.
    assert review_signals("token expires 2026-07-07", last_confirmed=_FRESH, now=_NOW) == []
    assert "expired-date" in review_signals(
        "token expires 2026-07-06", last_confirmed=_FRESH, now=_NOW
    )


def test_renewed_expiry_uses_latest_date() -> None:
    # Peer-review regression: the newest date wins — a renewal clears the flag.
    content = "expires 2026-01-01; RENEWED: expires 2027-01-01"
    assert review_signals(content, last_confirmed=_FRESH, now=_NOW) == []


def test_renewed_as_of_uses_latest_date() -> None:
    content = "as of 2026-06-01 two replicas; as of 2026-07-06 three replicas"
    assert review_signals(content, last_confirmed=_FRESH, now=_NOW) == []


def test_old_as_of_date_fires() -> None:
    confirmed = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC).isoformat()
    sig = review_signals(
        "as of 2026-06-01 there are two replicas", last_confirmed=confirmed, now=_NOW
    )
    assert "stale-as-of-date" in sig


def test_recent_as_of_date_does_not_fire() -> None:
    assert (
        review_signals("as of 2026-07-06 there are two replicas", last_confirmed=_FRESH, now=_NOW)
        == []
    )


def test_anchor_falls_back_to_created_at() -> None:
    sig = review_signals("waiting on Joe to merge it", created_at=_OLD, now=_NOW)
    assert "waiting-on" in sig


# --- timeless-type exemption --------------------------------------------------


def test_timeless_types_skip_gated_signals() -> None:
    # A pattern's technical prose ("blocked on disk I/O") is reference
    # material, not in-flight state — real-corpus false-positive regression.
    assert (
        review_signals(
            "apps blocked on disk I/O can't drain sockets",
            memory_type="pattern",
            last_confirmed=_OLD,
            now=_NOW,
        )
        == []
    )
    # A preference quoting status phrasing as an example is timeless too.
    assert (
        review_signals(
            'records like "PR #947 open, waiting on Joe" go silently stale',
            memory_type="preference",
            last_confirmed=_OLD,
            now=_NOW,
        )
        == []
    )


def test_point_in_time_types_still_flag() -> None:
    sig = review_signals(
        "waiting on Joe to approve", memory_type="workflow", last_confirmed=_OLD, now=_NOW
    )
    assert "waiting-on" in sig


def test_timeless_types_still_get_ungated_signals() -> None:
    # Dates carry their own clock — a pattern with a passed expiry still flags.
    sig = review_signals(
        "rotate the key, it expires 2026-06-29",
        memory_type="pattern",
        last_confirmed=_BEFORE_EXPIRY,
        now=_NOW,
    )
    assert "expired-date" in sig


# --- waiting-on needs a named agent, and nothing counts inside quotes --------


def test_waiting_on_technical_event_does_not_fire() -> None:
    """Real-corpus regressions. Both of these are mechanism descriptions, not
    status claims, and both flagged forever until this gate existed."""
    assert "waiting-on" not in _signals("the pipe mode blocks forever waiting for EOF")
    assert "waiting-on" not in _signals(
        "PodInitializing in all init containers = waiting for first init container image pull"
    )


def test_waiting_on_still_fires_for_a_named_agent() -> None:
    assert "waiting-on" in _signals("blocked on Joseph to finish the tofu cleanup")
    assert "waiting-on" in _signals("awaiting PR #947 before the flip")
    assert "waiting-on" in _signals("blocked on PROJ-52 sign-off")


def test_quoted_waiting_phrase_does_not_fire() -> None:
    """Narrating that a doc *said* "awaiting merge" is not a claim that it is."""
    assert "waiting-on" not in _signals(
        'fixed drift: PR #6 was still listed "In Progress / awaiting merge" though it merged'
    )


def test_apostrophes_are_not_quote_delimiters() -> None:
    """Regression: possessives and contractions must not read as quoting.

    "NOT yet committed/PR'd - awaiting Mr. Boomtastic's go" was silenced
    because PR'd and Boomtastic's were paired up as if they delimited a quote.
    """
    assert "waiting-on" in _signals("NOT yet committed/PR'd - awaiting Mr. Boomtastic's go")


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


@pytest.mark.asyncio
async def test_recall_and_search_also_carry_review_hints(server) -> None:
    """Peer-review regression: hint absence must mean the same thing on every
    read surface — recall and search stamp review_hints like context does."""
    flagged = _payload(
        await server.call_tool(
            "memory_store",
            {
                "content": "the deploy key expires 2026-06-29, rotate it",
                "title": "deploy key expiry",
                "type": "fact",
            },
        )
    )
    assert flagged["ok"]
    fid = flagged["memory"]["id"]

    rec = _payload(await server.call_tool("memory_recall", {"query": "deploy key expiry"}))
    rec_hit = next(m for m in rec["memories"] if m["id"] == fid)
    assert "expired-date" in rec_hit["review_hints"]

    srch = _payload(await server.call_tool("memory_search", {}))
    srch_hit = next(m for m in srch["memories"] if m["id"] == fid)
    assert "expired-date" in srch_hit["review_hints"]


@pytest.mark.asyncio
async def test_deprecated_memories_carry_no_review_hints(server) -> None:
    """A deprecation IS the reconciliation, so re-flagging it is asking for
    work that is already done.

    ``stats.compute_review`` has always excluded deprecated memories, but the
    read surfaces did not — so ``memory_stats`` would refuse to count a memory
    that ``memory_search(include_deprecated=True)`` still stamped a hint onto.
    Real cost: a corpus sweep counted 12 already-reconciled memories as live
    staleness and overstated the backlog by ~46%.
    """
    stored = _payload(
        await server.call_tool(
            "memory_store",
            {
                "content": "the vault token expires 2026-06-29, rotate it",
                "title": "vault token expiry",
                "type": "fact",
            },
        )
    )
    mid = stored["memory"]["id"]

    srch = _payload(await server.call_tool("memory_search", {"ids": mid}))
    assert "expired-date" in srch["memories"][0]["review_hints"]

    forgotten = _payload(await server.call_tool("memory_forget", {"memory_id": mid}))
    assert forgotten["ok"]

    after = _payload(await server.call_tool("memory_search", {"ids": mid}))
    assert "review_hints" not in after["memories"][0]

    stats = _payload(await server.call_tool("memory_stats", {}))
    assert stats["stats"]["review"]["review_suggested"] == 0


@pytest.mark.asyncio
async def test_retyping_clears_a_false_positive_hint(server) -> None:
    """The remedy agreed 2026-07-20 but impossible until now: memory_update
    had no `type` param, so an agent could not retype a misfiled memory and
    instead reworded the prose until the flag went away — silencing the signal
    while leaving the record degraded.

    Retyping to a timeless type is what clears a gated false positive; that the
    exemption works is covered by ``test_timeless_types_skip_gated_signals``.
    A gated signal needs a 14-day-old anchor, which a freshly stored memory
    cannot have, so this test proves the retype itself lands and is lossless.
    """
    content = "jira-cli hangs: with no TTY it blocks forever waiting for Joe's input"
    stored = _payload(
        await server.call_tool(
            "memory_store",
            {"content": content, "title": "jira-cli TTY hang", "type": "workflow"},
        )
    )
    mid = stored["memory"]["id"]
    assert stored["memory"]["type"] == "workflow"

    retyped = _payload(
        await server.call_tool("memory_update", {"memory_id": mid, "type": "pattern"})
    )
    assert retyped["ok"]
    assert retyped["memory"]["type"] == "pattern"

    # The retyped memory is now exempt from gated signals at any anchor age.
    assert review_signals(content, memory_type="pattern", last_confirmed=_OLD, now=_NOW) == []
    assert "waiting-on" in review_signals(
        content, memory_type="workflow", last_confirmed=_OLD, now=_NOW
    )

    after = _payload(await server.call_tool("memory_search", {"ids": mid}))
    assert "review_hints" not in after["memories"][0]
    # The content is untouched — the fix was the filing, not the prose.
    assert "waiting for Joe's input" in after["memories"][0]["content"]


@pytest.mark.asyncio
async def test_memory_update_rejects_an_invalid_type(server) -> None:
    stored = _payload(
        await server.call_tool(
            "memory_store",
            {"content": "body", "title": "t", "type": "fact"},
        )
    )
    bad = _payload(
        await server.call_tool(
            "memory_update", {"memory_id": stored["memory"]["id"], "type": "journal"}
        )
    )
    assert not bad.get("ok")
    assert "invalid type" in bad["error"]
