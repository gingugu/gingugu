"""Tests for enumerating the graph backlog through the tool surface.

The gap these pin shut: ``memory_stats`` reported that 45 memories were cut out
of the graph and nothing could name one of them. An orphan is reachable only by
direct search — spreading activation can never wake it — so the count described
a real retrieval cost with no way to work through it. Reconnecting meant
querying the live database by hand, which is what a memory server exists to
avoid. Every test here asks what a reconnection sweep actually asks — "which
memories are cut off, and can I read them?" — through the tools alone.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "orphans.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "gingugu")
    from gingugu.server import build_server

    return build_server()


async def _store(server, title: str, **extra) -> str:
    payload = {"title": title, "content": f"content of {title}", "type": "fact", **extra}
    return _payload(await server.call_tool("memory_store", payload))["memory"]["id"]


async def _relate(server, source: str, target: str, rel_type: str = "caused_by") -> dict:
    return _payload(
        await server.call_tool(
            "memory_relate",
            {"source_id": source, "target_id": target, "relation_type": rel_type},
        )
    )


async def _graph(server, **kwargs) -> dict:
    return _payload(await server.call_tool("memory_stats", kwargs))["stats"]["graph"]


async def _search(server, **kwargs) -> dict:
    return _payload(await server.call_tool("memory_search", kwargs))


# --- stats: the sample enumerates the backlog -------------------------------


@pytest.mark.asyncio
async def test_stats_names_the_orphans_it_counts(server) -> None:
    """The gap itself: a count that identifies nobody."""
    a = await _store(server, "alpha")
    b = await _store(server, "beta")
    await _store(server, "gamma")
    await _relate(server, a, b)

    graph = await _graph(server)
    assert graph["orphans"] == 1
    assert [row["title"] for row in graph["orphan_sample"]] == ["gamma"]


@pytest.mark.asyncio
async def test_review_limit_raises_the_orphan_sample_too(server) -> None:
    """One knob for the whole reconciliation surface: review, claims, orphans."""
    for i in range(7):
        await _store(server, f"orphan-{i}")

    assert len((await _graph(server))["orphan_sample"]) == 5
    raised = await _graph(server, review_limit=100)
    assert (await _graph(server))["orphans"] == 7
    assert len(raised["orphan_sample"]) == 7


@pytest.mark.asyncio
async def test_reconnecting_an_orphan_removes_it_from_the_sample(server) -> None:
    """The metric moves when the work is done — the point of surfacing it."""
    a = await _store(server, "alpha")
    b = await _store(server, "beta")
    assert (await _graph(server))["orphans"] == 2

    await _relate(server, a, b)

    graph = await _graph(server)
    assert graph["orphans"] == 0
    assert graph["orphan_sample"] == []


# --- search: the same set, with the bodies ----------------------------------


@pytest.mark.asyncio
async def test_search_returns_only_orphans_without_a_query(server) -> None:
    """A query-less orphan sweep is the working mode: you are not looking for a
    topic, you are working through a backlog."""
    a = await _store(server, "alpha")
    b = await _store(server, "beta")
    await _store(server, "gamma")
    await _relate(server, a, b)

    result = await _search(server, orphans=True)
    assert result["count"] == 1
    assert result["memories"][0]["title"] == "gamma"


@pytest.mark.asyncio
async def test_search_orphans_agrees_with_the_stats_count(server) -> None:
    """A count and its enumeration must be counting the same thing. They share
    one predicate precisely so they cannot drift apart."""
    a = await _store(server, "alpha")
    b = await _store(server, "beta")
    for i in range(4):
        await _store(server, f"orphan-{i}")
    await _relate(server, a, b)

    assert (await _graph(server))["orphans"] == 4
    assert (await _search(server, orphans=True, limit=50))["count"] == 4


@pytest.mark.asyncio
async def test_search_orphans_composes_with_other_filters(server) -> None:
    """Same contract as ``claims``: a filter, not a separate mode."""
    await _store(server, "orphan-fact", type="fact")
    await _store(server, "orphan-bug", type="bug")
    a = await _store(server, "linked-fact", type="fact")
    b = await _store(server, "linked-other", type="fact")
    await _relate(server, a, b)

    result = await _search(server, orphans=True, type="fact", limit=50)
    assert [m["title"] for m in result["memories"]] == ["orphan-fact"]


@pytest.mark.asyncio
async def test_search_orphans_composes_with_a_query(server) -> None:
    """The filter narrows a real search rather than replacing it."""
    await _store(server, "harbour charts")
    await _store(server, "harbour depths")
    a = await _store(server, "harbour lights")
    b = await _store(server, "unrelated matter")
    await _relate(server, a, b)

    result = await _search(server, query="harbour", orphans=True, limit=50)
    titles = {m["title"] for m in result["memories"]}
    assert titles == {"harbour charts", "harbour depths"}


@pytest.mark.asyncio
async def test_search_orphans_scopes_to_a_namespace(server) -> None:
    await _store(server, "here-orphan")
    await _store(server, "there-orphan", namespace="elsewhere")

    result = await _search(server, orphans=True, namespace="elsewhere", limit=50)
    assert [m["title"] for m in result["memories"]] == ["there-orphan"]


@pytest.mark.asyncio
async def test_search_orphans_defaults_off(server) -> None:
    """The filter is opt-in; an ordinary search is unchanged by its existence."""
    a = await _store(server, "alpha")
    b = await _store(server, "beta")
    await _store(server, "gamma")
    await _relate(server, a, b)

    assert (await _search(server, limit=50))["count"] == 3


@pytest.mark.asyncio
async def test_relating_a_found_orphan_takes_it_out_of_the_filter(server) -> None:
    """End to end: enumerate, reconnect, and confirm the backlog shrank."""
    a = await _store(server, "alpha")
    orphan = (await _search(server, orphans=True, limit=50))["memories"]
    assert len(orphan) == 1

    b = await _store(server, "beta")
    await _relate(server, a, b)

    assert (await _search(server, orphans=True, limit=50))["count"] == 0
