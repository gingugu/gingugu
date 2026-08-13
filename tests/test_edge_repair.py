"""Tests for enumerating and repairing graph edges through the tool surface.

The gap these pin shut: ``memory_relate`` created edges and nothing could
remove or relabel one, so a mislabelled edge was permanent for the life of both
memories — and kept competing for one of the three spreading-activation slots
on each. ``memory_stats`` could report that a graph was mostly ``related_to``
but nothing could say *which* edges those were. Repair meant hand-written SQL
against the live database. Every test here asks what a repair sweep actually
asks — "which edges are wrong, and can I fix them?" — through the tools alone.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "edges.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    from gingugu.server import build_server

    return build_server()


async def _store(server, title: str, **extra) -> str:
    payload = {"title": title, "content": f"content of {title}", "type": "fact", **extra}
    return _payload(await server.call_tool("memory_store", payload))["memory"]["id"]


async def _relate(server, source: str, target: str, rel_type: str) -> dict:
    return _payload(
        await server.call_tool(
            "memory_relate",
            {"source_id": source, "target_id": target, "relation_type": rel_type},
        )
    )


async def _unrelate(server, **kwargs) -> dict:
    return _payload(await server.call_tool("memory_unrelate", kwargs))


async def _edges(server, **kwargs) -> dict:
    return _payload(await server.call_tool("memory_edges", kwargs))


# --- enumeration: you cannot repair what you cannot read --------------------


@pytest.mark.asyncio
async def test_edges_lists_them_with_both_endpoint_titles(server) -> None:
    """The discovery half of the gap. Stats said '2 related_to edges' and the
    caller had nowhere to go to find out which."""
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")

    result = await _edges(server)
    assert result["total"] == 1
    edge = result["edges"][0]
    assert edge["source_title"] == "alpha"
    assert edge["target_title"] == "beta"
    assert edge["relation_type"] == "related_to"
    assert edge["source_namespace"] == "gingugu"


@pytest.mark.asyncio
async def test_edges_filters_by_relation_type(server) -> None:
    a, b, c = (
        await _store(server, "alpha"),
        await _store(server, "beta"),
        await _store(server, "gamma"),
    )
    await _relate(server, a, b, "related_to")
    await _relate(server, a, c, "supersedes")

    result = await _edges(server, relation_type="related_to")
    assert result["total"] == 1
    assert result["edges"][0]["target_title"] == "beta"


@pytest.mark.asyncio
async def test_edges_filters_to_one_memory_in_either_direction(server) -> None:
    a, b, c = (
        await _store(server, "alpha"),
        await _store(server, "beta"),
        await _store(server, "gamma"),
    )
    await _relate(server, a, b, "related_to")  # b is the target
    await _relate(server, b, c, "caused_by")  # b is the source

    result = await _edges(server, memory_id=b)
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_edges_reports_degree_so_unreachable_edges_are_visible(server) -> None:
    """Degree is what decides whether an edge can ever fire, so it has to be in
    the row a caller judges the edge from."""
    hub = await _store(server, "hub")
    for name in ("one", "two", "three", "four"):
        await _relate(server, hub, await _store(server, name), "related_to")

    result = await _edges(server, memory_id=hub)
    assert all(edge["source_degree"] == 4 for edge in result["edges"])


@pytest.mark.asyncio
async def test_edges_paginates_deterministically(server) -> None:
    a = await _store(server, "alpha")
    for name in ("beta", "gamma", "delta"):
        await _relate(server, a, await _store(server, name), "related_to")

    first = await _edges(server, limit=2)
    second = await _edges(server, limit=2, offset=2)
    assert first["total"] == 3
    assert first["returned"] == 2
    assert second["returned"] == 1
    seen = [e["target_title"] for e in first["edges"] + second["edges"]]
    assert seen == sorted(seen)  # ordering is stable, nothing seen twice


@pytest.mark.asyncio
async def test_edges_rejects_unknown_namespace(server) -> None:
    result = await _edges(server, namespace="no-such-namespace")
    assert result["ok"] is False


# --- retype: the repair that keeps the edge -------------------------------


@pytest.mark.asyncio
async def test_retype_relabels_in_place(server) -> None:
    """The common case: right connection, wrong label."""
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")

    result = await _unrelate(
        server,
        source_id=a,
        target_id=b,
        relation_type="related_to",
        new_relation_type="caused_by",
    )
    assert result["ok"] is True
    assert result["outcomes"] == {"retyped": 1}

    edges = await _edges(server)
    assert edges["total"] == 1
    assert edges["edges"][0]["relation_type"] == "caused_by"


@pytest.mark.asyncio
async def test_retype_preserves_creation_time(server) -> None:
    """Provenance survives a relabel: the link was genuinely drawn when it was,
    and the graph should not claim otherwise."""
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")
    before = (await _edges(server))["edges"][0]["created_at"]

    await _unrelate(
        server,
        source_id=a,
        target_id=b,
        relation_type="related_to",
        new_relation_type="supersedes",
    )
    assert (await _edges(server))["edges"][0]["created_at"] == before


@pytest.mark.asyncio
async def test_retype_onto_an_existing_type_merges_and_says_so(server) -> None:
    """Two edges collapse into one. The count drops, and the outcome reports
    ``merged`` rather than pretending a plain retype happened."""
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")
    await _relate(server, a, b, "caused_by")

    result = await _unrelate(
        server,
        source_id=a,
        target_id=b,
        relation_type="related_to",
        new_relation_type="caused_by",
    )
    assert result["outcomes"] == {"merged": 1}
    edges = await _edges(server)
    assert edges["total"] == 1
    assert edges["edges"][0]["relation_type"] == "caused_by"


@pytest.mark.asyncio
async def test_retype_of_a_missing_edge_reports_not_found(server) -> None:
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    result = await _unrelate(
        server,
        source_id=a,
        target_id=b,
        relation_type="related_to",
        new_relation_type="caused_by",
    )
    assert result["ok"] is True
    assert result["outcomes"] == {"not_found": 1}


@pytest.mark.asyncio
async def test_retype_without_current_type_is_rejected(server) -> None:
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")
    result = await _unrelate(server, source_id=a, target_id=b, new_relation_type="caused_by")
    assert result["ok"] is False
    assert "relation_type" in result["error"]


# --- delete: the repair that removes the edge -----------------------------


@pytest.mark.asyncio
async def test_delete_removes_one_named_edge(server) -> None:
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")
    await _relate(server, a, b, "caused_by")

    result = await _unrelate(server, source_id=a, target_id=b, relation_type="related_to")
    assert result["outcomes"] == {"deleted": 1}
    edges = await _edges(server)
    assert edges["total"] == 1
    assert edges["edges"][0]["relation_type"] == "caused_by"


@pytest.mark.asyncio
async def test_delete_without_type_removes_every_edge_between_the_pair(server) -> None:
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")
    await _relate(server, a, b, "caused_by")

    result = await _unrelate(server, source_id=a, target_id=b)
    assert result["outcomes"] == {"deleted": 1}
    assert set(result["results"][0]["removed_types"]) == {"related_to", "caused_by"}
    assert (await _edges(server))["total"] == 0


@pytest.mark.asyncio
async def test_delete_leaves_the_memories_alone(server) -> None:
    """Removing an edge is not forgetting a memory."""
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")
    await _unrelate(server, source_id=a, target_id=b, relation_type="related_to")

    found = _payload(await server.call_tool("memory_search", {"ids": f"{a},{b}"}))
    assert found["count"] == 2


@pytest.mark.asyncio
async def test_self_edge_is_rejected(server) -> None:
    a = await _store(server, "alpha")
    result = await _unrelate(server, source_id=a, target_id=a, relation_type="related_to")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_missing_ids_are_rejected(server) -> None:
    result = await _unrelate(server, relation_type="related_to")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_invalid_relation_type_is_rejected(server) -> None:
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    result = await _unrelate(server, source_id=a, target_id=b, relation_type="vaguely_about")
    assert result["ok"] is False
    assert "invalid relation_type" in result["error"]


# --- batch: reviewed decisions, submitted together ------------------------


@pytest.mark.asyncio
async def test_batch_mixes_retypes_and_deletes(server) -> None:
    """What a repair sweep actually submits: each edge judged on its own merits,
    sent in one call to save round-trips rather than judgment."""
    a, b, c, d = (
        await _store(server, "alpha"),
        await _store(server, "beta"),
        await _store(server, "gamma"),
        await _store(server, "delta"),
    )
    await _relate(server, a, b, "related_to")
    await _relate(server, c, d, "related_to")

    result = await _unrelate(
        server,
        edges=[
            {
                "source_id": a,
                "target_id": b,
                "relation_type": "related_to",
                "new_relation_type": "supersedes",
            },
            {"source_id": c, "target_id": d, "relation_type": "related_to"},
        ],
    )
    assert result["processed"] == 2
    assert result["outcomes"] == {"retyped": 1, "deleted": 1}

    edges = await _edges(server)
    assert edges["total"] == 1
    assert edges["edges"][0]["relation_type"] == "supersedes"


@pytest.mark.asyncio
async def test_batch_validation_failure_writes_nothing(server) -> None:
    """A malformed op fails the whole call. Half a repaired graph is worse than
    an unrepaired one, because nothing records where the sweep stopped."""
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")

    result = await _unrelate(
        server,
        edges=[
            {"source_id": a, "target_id": b, "relation_type": "related_to"},
            {"source_id": a, "relation_type": "related_to"},  # no target
        ],
    )
    assert result["ok"] is False
    assert (await _edges(server))["total"] == 1  # first op never ran


@pytest.mark.asyncio
async def test_batch_names_the_offending_index_on_a_bad_op(server) -> None:
    """A 100-op sweep is unusable if the rejection does not say which op failed."""
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    result = await _unrelate(
        server,
        edges=[
            {"source_id": a, "target_id": b, "relation_type": "related_to"},
            {"source_id": a, "target_id": b, "relation_type": "vaguely_about"},
        ],
    )
    assert result["ok"] is False
    assert "invalid relation_type" in result["error"]


@pytest.mark.asyncio
async def test_batch_rejects_a_self_edge(server) -> None:
    a = await _store(server, "alpha")
    result = await _unrelate(server, edges=[{"source_id": a, "target_id": a}])
    assert result["ok"] is False
    assert "self-edge" in result["error"]


@pytest.mark.asyncio
async def test_batch_rejects_an_empty_list(server) -> None:
    """A caller that built its op list from an empty collection fails loudly
    rather than reporting a successful no-op sweep."""
    result = await _unrelate(server, edges=[])
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_batch_is_capped(server) -> None:
    from gingugu.handlers.relations import MAX_BATCH_EDGES

    ops = [
        {"source_id": f"a{i}", "target_id": f"b{i}", "relation_type": "related_to"}
        for i in range(MAX_BATCH_EDGES + 1)
    ]
    result = await _unrelate(server, edges=ops)
    assert result["ok"] is False
    assert str(MAX_BATCH_EDGES) in result["error"]


@pytest.mark.asyncio
async def test_batch_and_single_edge_fields_are_mutually_exclusive(server) -> None:
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    result = await _unrelate(
        server,
        source_id=a,
        target_id=b,
        edges=[{"source_id": a, "target_id": b}],
    )
    assert result["ok"] is False


# --- dry run --------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_previews_without_writing(server) -> None:
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")

    result = await _unrelate(
        server,
        source_id=a,
        target_id=b,
        relation_type="related_to",
        new_relation_type="caused_by",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["outcomes"] == {"would_retype": 1}

    edges = await _edges(server)
    assert edges["edges"][0]["relation_type"] == "related_to"  # untouched


@pytest.mark.asyncio
async def test_dry_run_covers_deletes_too(server) -> None:
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")

    result = await _unrelate(
        server, source_id=a, target_id=b, relation_type="related_to", dry_run=True
    )
    assert result["outcomes"] == {"would_delete": 1}
    assert (await _edges(server))["total"] == 1


# --- the repaired graph is the graph retrieval sees ------------------------


@pytest.mark.asyncio
async def test_deleting_an_edge_stops_spreading_activation_through_it(server) -> None:
    """The whole reason edge repair matters: a wrong edge is not cosmetic, it
    steers what recall surfaces until it is removed."""
    # Deliberately unrelated wording: the only route from the query to
    # ``postgres`` is the edge itself, so its removal is what the test observes.
    a = await _store(server, "kestrel migration runbook")
    b = await _store(server, "postgres vacuum tuning")
    await _relate(server, a, b, "related_to")

    query = {"query": "kestrel migration runbook", "include_related": True}
    before = _payload(await server.call_tool("memory_recall", query))
    assert any(m["id"] == b for m in before["memories"])

    await _unrelate(server, source_id=a, target_id=b, relation_type="related_to")

    after = _payload(await server.call_tool("memory_recall", query))
    assert not any(m["id"] == b for m in after["memories"])


@pytest.mark.asyncio
async def test_repair_moves_the_high_signal_ratio_stats_report(server) -> None:
    """Retyping is measurable in the same metric that motivated the sweep."""
    a, b = await _store(server, "alpha"), await _store(server, "beta")
    await _relate(server, a, b, "related_to")

    graph = _payload(await server.call_tool("memory_stats", {}))["stats"]["graph"]
    assert graph["high_signal_ratio"] == 0.0

    await _unrelate(
        server,
        source_id=a,
        target_id=b,
        relation_type="related_to",
        new_relation_type="supersedes",
    )

    graph = _payload(await server.call_tool("memory_stats", {}))["stats"]["graph"]
    assert graph["high_signal_ratio"] == 1.0
