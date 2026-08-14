"""Tests for enumerating the state-claim backlog through the tool surface.

The gap these pin shut: ``memory_stats`` reported a count of open claims and
then handed back a sample containing only the *contradicted* subset, so a
namespace could report five open claims and offer no way to learn which five.
Reconciling meant querying the live database by hand. Every test here asks the
question a reconciliation sweep actually asks — "which memories still assert
something is open, and can I read them?" — through the tools alone.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "enumeration.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    from gingugu.server import build_server

    return build_server()


async def _store(server, title: str, content: str, **extra) -> dict:
    payload = {"title": title, "content": content, "type": "workflow", **extra}
    return _payload(await server.call_tool("memory_store", payload))["memory"]


async def _claims(server, **kwargs) -> dict:
    return _payload(await server.call_tool("memory_stats", kwargs))["stats"]["claims"]


# --- stats: the sample enumerates the backlog -------------------------------


@pytest.mark.asyncio
async def test_sample_lists_open_claims_not_just_contradicted_ones(server) -> None:
    """The whole bug. Two open claims, neither contradicted — the old sample
    was empty here and the caller had nowhere to go."""
    await _store(server, "session log", "PR #10 open, waiting on review")
    await _store(server, "other log", "PR #20 awaiting review")

    claims = await _claims(server)
    assert claims["open"] == 2
    assert claims["contradicted"] == 0
    assert {row["ref"] for row in claims["sample"]} == {"gingugu#10", "gingugu#20"}
    assert all(row["contradicted"] is False for row in claims["sample"])


@pytest.mark.asyncio
async def test_contradicted_claims_sort_first_and_are_tagged(server) -> None:
    """Ordering carries the priority: a contradicted claim is answerable right
    now from what the brain already holds, so it leads the sample."""
    await _store(server, "old log", "PR #10 open, not merged yet")
    await _store(server, "plain log", "PR #20 awaiting review")
    await _store(server, "release", "PR #10 merged to main")

    claims = await _claims(server)
    assert claims["contradicted"] == 1
    assert claims["sample"][0] == {
        "id": claims["sample"][0]["id"],
        "title": "old log",
        "ref": "gingugu#10",
        "contradicted": True,
    }
    assert [row["contradicted"] for row in claims["sample"]] == [True, False]


@pytest.mark.asyncio
async def test_open_actionable_explains_the_gap_against_open(server) -> None:
    """``open`` counts claims on deprecated memories; the sample does not.
    Without the second count that difference reads as a missing row."""
    spent = await _store(server, "spent resume", "PR #10 open, waiting on review")
    await _store(server, "live log", "PR #20 awaiting review")
    await server.call_tool("memory_update", {"memory_id": spent["id"], "confidence": "deprecated"})

    claims = await _claims(server)
    assert claims["open"] == 2
    assert claims["open_actionable"] == 1
    assert [row["ref"] for row in claims["sample"]] == ["gingugu#20"]


@pytest.mark.asyncio
async def test_review_limit_raises_the_claims_sample_cap(server) -> None:
    """Default 5 keeps a session-start call cheap; a sweep raises it to
    enumerate the whole backlog in one shot."""
    for num in range(1, 8):
        await _store(server, f"log {num}", f"PR #{num}0 open, awaiting review")

    default = await _claims(server)
    assert default["open_actionable"] == 7
    assert len(default["sample"]) == 5  # capped, but the count still tells the truth

    raised = await _claims(server, review_limit=100)
    assert raised["open_actionable"] == 7
    assert len(raised["sample"]) == 7


@pytest.mark.asyncio
async def test_resolved_claims_leave_the_backlog(server) -> None:
    stale = await _store(server, "session log", "PR #10 open, waiting on review")
    await server.call_tool(
        "memory_update", {"memory_id": stale["id"], "resolve_claims": "gingugu#10"}
    )
    claims = await _claims(server)
    assert claims["open"] == 0
    assert claims["sample"] == []


# --- memory_search(claims=...) ----------------------------------------------


@pytest.mark.asyncio
async def test_search_filters_to_memories_with_open_claims(server) -> None:
    await _store(server, "session log", "PR #10 open, waiting on review")
    await _store(server, "unrelated", "notes with no refs at all")

    found = _payload(await server.call_tool("memory_search", {"claims": "open"}))
    assert [m["title"] for m in found["memories"]] == ["session log"]
    assert found["memories"][0]["content"]  # full bodies, not just ids


@pytest.mark.asyncio
async def test_search_narrows_to_contradicted_claims(server) -> None:
    await _store(server, "old log", "PR #10 open, not merged yet")
    await _store(server, "plain log", "PR #20 awaiting review")
    await _store(server, "release", "PR #10 merged to main")

    hits = _payload(await server.call_tool("memory_search", {"claims": "contradicted"}))
    assert [m["title"] for m in hits["memories"]] == ["old log"]


@pytest.mark.asyncio
async def test_claims_filter_composes_with_a_query(server) -> None:
    """Not a separate listing mode: the filter rides the normal search path,
    so it composes with the query and every other filter."""
    await _store(server, "deploy log", "PR #10 open, blocked on the helm chart")
    await _store(server, "schema log", "PR #20 open, blocked on the migration")

    hits = _payload(
        await server.call_tool("memory_search", {"query": "helm chart", "claims": "open"})
    )
    assert [m["title"] for m in hits["memories"]] == ["deploy log"]


@pytest.mark.asyncio
async def test_claims_filter_composes_with_type_and_namespace(server) -> None:
    await _store(server, "workflow log", "PR #10 open, waiting on review")
    await _store(server, "decision log", "PR #20 open, waiting on review", type="decision")

    hits = _payload(
        await server.call_tool(
            "memory_search", {"claims": "open", "type": "decision", "namespace": "gingugu"}
        )
    )
    assert [m["title"] for m in hits["memories"]] == ["decision log"]


@pytest.mark.asyncio
async def test_resolved_claims_drop_out_of_the_search_filter(server) -> None:
    stale = await _store(server, "session log", "PR #10 open, waiting on review")
    await server.call_tool("memory_update", {"memory_id": stale["id"], "resolve_claims": "all"})
    hits = _payload(await server.call_tool("memory_search", {"claims": "open"}))
    assert hits["memories"] == []


@pytest.mark.asyncio
async def test_invalid_claims_value_is_rejected(server) -> None:
    out = _payload(await server.call_tool("memory_search", {"claims": "sideways"}))
    assert out["ok"] is False
    assert "invalid claims" in out["error"]


@pytest.mark.asyncio
async def test_ids_fetch_still_ignores_every_other_filter(server) -> None:
    """``ids`` is the precise-fetch path — the caller named the memory, so a
    claims filter must not silently drop it."""
    plain = await _store(server, "unrelated", "notes with no refs at all")
    found = _payload(
        await server.call_tool("memory_search", {"ids": plain["id"], "claims": "open"})
    )
    assert [m["id"] for m in found["memories"]] == [plain["id"]]


# --- the loop end to end ----------------------------------------------------


@pytest.mark.asyncio
async def test_full_reconciliation_loop_without_leaving_the_tools(server) -> None:
    """stats -> search -> update, with the prose untouched. This is the whole
    point of the feature: the sweep that forced raw SQL now runs on tools."""
    prose = "Session Jun 15: PR #10, open, NOT merged yet. Waiting on Joe."
    await _store(server, "session log", prose)

    backlog = await _claims(server, review_limit=100)
    refs = [row["ref"] for row in backlog["sample"]]
    assert refs == ["gingugu#10"]

    bodies = _payload(await server.call_tool("memory_search", {"claims": "open"}))
    target = bodies["memories"][0]
    assert target["content"] == prose

    resolved = _payload(
        await server.call_tool(
            "memory_update", {"memory_id": target["id"], "resolve_claims": ",".join(refs)}
        )
    )
    assert resolved["resolved_claims"] == ["gingugu#10"]
    assert resolved["memory"]["content"] == prose  # byte-identical

    assert (await _claims(server))["open_actionable"] == 0


# --- unverified: named, but asserting nothing -------------------------------


@pytest.mark.asyncio
async def test_unverified_refs_stay_out_of_every_open_count(server) -> None:
    """The guarantee the whole design rests on.

    Recording state-less refs must not move ``open`` by one. Measured on the
    live corpus this state covers ~225 refs against 223 real claims, so a leak
    here would more than double the backlog with prose about finished work.
    """
    await _store(server, "deliverables", "Branch done, PR #1: shipped it")
    await _store(server, "notes", "see PR #40 for context")
    await _store(server, "real backlog", "PR #10 open, waiting on review")

    claims = await _claims(server)
    assert claims["unverified"] == 2
    assert claims["open"] == 1
    assert claims["open_actionable"] == 1
    assert [row["ref"] for row in claims["sample"]] == ["gingugu#10"]


@pytest.mark.asyncio
async def test_unverified_is_enumerable_on_its_own(server) -> None:
    """Invisible was the bug; a backlog it is not. So it gets its own filter."""
    quiet = await _store(server, "notes", "see PR #40 for context")
    await _store(server, "real backlog", "PR #10 open, waiting on review")

    found = _payload(await server.call_tool("memory_search", {"claims": "unverified"}))
    assert [m["id"] for m in found["memories"]] == [quiet["id"]]


@pytest.mark.asyncio
async def test_open_and_unverified_filters_return_disjoint_sets(server) -> None:
    """Neither sweep may quietly include the other's rows."""
    quiet = await _store(server, "notes", "see PR #40 for context")
    loud = await _store(server, "real backlog", "PR #10 open, waiting on review")

    for mode, expected in (("unverified", quiet["id"]), ("open", loud["id"])):
        found = _payload(await server.call_tool("memory_search", {"claims": mode}))
        assert [m["id"] for m in found["memories"]] == [expected], mode


@pytest.mark.asyncio
async def test_resolve_all_never_sweeps_an_unverified_ref(server) -> None:
    """ "all" means everything this memory says is in flight.

    An unverified ref says nothing, so closing it under "all" would record that
    the caller checked something they never looked at.
    """
    mem = await _store(server, "mixed", "PR #10 open, waiting on review. Also see PR #40.")

    resolved = _payload(
        await server.call_tool("memory_update", {"memory_id": mem["id"], "resolve_claims": "all"})
    )
    assert resolved["resolved_claims"] == ["gingugu#10"]

    claims = await _claims(server)
    assert claims["open_actionable"] == 0
    assert claims["unverified"] == 1  # untouched


@pytest.mark.asyncio
async def test_naming_an_unverified_ref_explicitly_does_resolve_it(server) -> None:
    """The honest way to say "I looked, and it merged"."""
    mem = await _store(server, "deliverables", "Branch done, PR #40: shipped it")
    assert (await _claims(server))["unverified"] == 1

    resolved = _payload(
        await server.call_tool(
            "memory_update", {"memory_id": mem["id"], "resolve_claims": "gingugu#40"}
        )
    )
    assert resolved["resolved_claims"] == ["gingugu#40"]
    assert (await _claims(server))["unverified"] == 0


@pytest.mark.asyncio
async def test_an_unverified_ref_cannot_contradict_a_later_memory(server) -> None:
    """Contradiction needs two assertions. Silence is not one of them."""
    await _store(server, "notes", "see PR #40 for context")
    await _store(server, "release", "PR #40 merged to main")

    claims = await _claims(server)
    assert claims["contradicted"] == 0
    assert claims["sample"] == []
