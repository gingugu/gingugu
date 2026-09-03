"""The dream pass over the live MCP tool surface.

The tests that matter here are the refusals. The passes stop short of a
judgment on purpose - an ``edge`` proposal carries no relation type, a
``cluster`` carries no name - so accepting one has to demand the missing half.
If accepting an untyped pair quietly wrote ``related_to``, the arithmetic would
have picked a relation type after all, by default, and the guarantee that no
computation decides what is in the brain would be gone.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "dream.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "dreams")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _store(server, title: str, content: str = "") -> str:
    res = _payload(
        await server.call_tool(
            "memory_store",
            {"title": title, "content": content or f"body of {title}", "type": "fact"},
        )
    )
    assert res["ok"]
    return res["memory"]["id"]


async def _hub(server, spokes: int = 6) -> tuple[str, list[str]]:
    hub = await _store(server, "hub")
    leaves = []
    for i in range(spokes):
        leaf = await _store(server, f"leaf-{i}")
        leaves.append(leaf)
        await server.call_tool(
            "memory_relate",
            {"source_id": leaf, "target_id": hub, "relation_type": "caused_by"},
        )
    return hub, leaves


async def test_run_then_list_surfaces_the_hub(server) -> None:
    hub, _ = await _hub(server)

    run = _payload(await server.call_tool("memory_dream", {"action": "run"}))
    assert run["ok"]
    assert run["graph"]["nodes"] == 7
    assert run["passes"]["centrality"]["staged"] >= 1

    listed = _payload(await server.call_tool("memory_dream", {"action": "list", "kind": "core"}))
    assert listed["ok"]
    subjects = [p["subject_id"] for p in listed["proposals"]]
    assert hub in subjects
    top = next(p for p in listed["proposals"] if p["subject_id"] == hub)
    assert top["evidence"]["degree"] == 6
    assert top["evidence"]["times_baseline"] > 1
    assert top["subject_title"] == "hub", "a proposal must be readable without a second lookup"


async def test_accepting_an_edge_without_a_type_is_refused(server) -> None:
    a = await _store(server, "alpha", "shared vocabulary about deployment rollbacks")
    b = await _store(server, "beta", "shared vocabulary about deployment rollbacks")

    # Stage the pair directly: the point under test is the accept path, not
    # whether this particular corpus clears the similarity floor.
    from gingugu.config import load_config
    from gingugu.database import Database
    from gingugu.proposals import ProposalQueue, ordered_pair

    db_conn = Database(load_config().db_path).connect()
    subject, obj = ordered_pair(a, b)
    ProposalQueue(db_conn).stage(
        pass_name="orphans",
        kind="edge",
        subject_id=subject,
        object_id=obj,
        score=0.9,
        evidence={"relation_type": None},
    )

    listed = _payload(await server.call_tool("memory_dream", {"action": "list", "kind": "edge"}))
    proposal_id = listed["proposals"][0]["id"]

    refused = _payload(
        await server.call_tool("memory_dream", {"action": "accept", "proposal_id": proposal_id})
    )
    assert refused["ok"] is False
    assert "relation_type" in refused["error"]

    still_pending = _payload(
        await server.call_tool("memory_dream", {"action": "list", "kind": "edge"})
    )
    assert still_pending["proposals"][0]["status"] == "pending"

    accepted = _payload(
        await server.call_tool(
            "memory_dream",
            {"action": "accept", "proposal_id": proposal_id, "relation_type": "supersedes"},
        )
    )
    assert accepted["ok"]
    assert "supersedes" in accepted["applied"]["edge"]

    edges = _payload(await server.call_tool("memory_edges", {"memory_id": a}))
    assert any(e["relation_type"] == "supersedes" for e in edges["edges"])


async def test_accepting_a_core_proposal_pins_the_memory(server) -> None:
    hub, _ = await _hub(server)
    await server.call_tool("memory_dream", {"action": "run"})

    listed = _payload(await server.call_tool("memory_dream", {"action": "list", "kind": "core"}))
    proposal = next(p for p in listed["proposals"] if p["subject_id"] == hub)

    accepted = _payload(
        await server.call_tool("memory_dream", {"action": "accept", "proposal_id": proposal["id"]})
    )
    assert accepted["ok"]
    assert accepted["applied"]["pinned"] == hub

    pins = _payload(await server.call_tool("memory_search", {"pinned": True}))
    assert [m["id"] for m in pins["memories"]] == [hub]


async def test_rejecting_settles_a_proposal_permanently(server) -> None:
    hub, _ = await _hub(server)
    await server.call_tool("memory_dream", {"action": "run"})
    listed = _payload(await server.call_tool("memory_dream", {"action": "list", "kind": "core"}))
    proposal_id = listed["proposals"][0]["id"]

    rejected = _payload(
        await server.call_tool("memory_dream", {"action": "reject", "proposal_id": proposal_id})
    )
    assert rejected["ok"]

    # A second run recomputes the same graph and reaches the same conclusion.
    await server.call_tool("memory_dream", {"action": "run"})
    again = _payload(await server.call_tool("memory_dream", {"action": "list", "kind": "core"}))
    assert proposal_id not in [p["id"] for p in again["proposals"]]

    twice = _payload(
        await server.call_tool("memory_dream", {"action": "reject", "proposal_id": proposal_id})
    )
    assert twice["ok"] is False
    assert "already rejected" in twice["error"]


async def test_bad_action_and_missing_id_are_errors_not_crashes(server) -> None:
    bad = _payload(await server.call_tool("memory_dream", {"action": "hallucinate"}))
    assert bad["ok"] is False

    missing = _payload(await server.call_tool("memory_dream", {"action": "accept"}))
    assert missing["ok"] is False
    assert "proposal_id" in missing["error"]

    unknown_ns = _payload(
        await server.call_tool("memory_dream", {"action": "list", "namespace": "nope"})
    )
    assert unknown_ns["ok"] is False


async def test_accepting_a_cluster_without_a_name_is_refused(server, tmp_path) -> None:
    """Membership is countable; what the group is *about* is prose."""
    from gingugu.config import load_config
    from gingugu.database import Database
    from gingugu.proposals import ProposalQueue

    members = [await _store(server, f"m{i}") for i in range(3)]
    db_conn = Database(load_config().db_path).connect()
    ProposalQueue(db_conn).stage(
        pass_name="clusters",
        kind="cluster",
        subject_id=members[0],
        score=1.0,
        evidence={"size": 3, "members": members},
    )
    proposal_id = _payload(
        await server.call_tool("memory_dream", {"action": "list", "kind": "cluster"})
    )["proposals"][0]["id"]

    refused = _payload(
        await server.call_tool("memory_dream", {"action": "accept", "proposal_id": proposal_id})
    )
    assert refused["ok"] is False
    assert "tag" in refused["error"]

    accepted = _payload(
        await server.call_tool(
            "memory_dream",
            {"action": "accept", "proposal_id": proposal_id, "tag": "release-discipline"},
        )
    )
    assert accepted["ok"]
    assert accepted["applied"] == {"tagged": 3, "tag": "release-discipline"}

    tagged = _payload(await server.call_tool("memory_search", {"tags": "release-discipline"}))
    assert sorted(m["id"] for m in tagged["memories"]) == sorted(members)


def test_dream_cli_runs_and_summarises(monkeypatch, tmp_path, capsys) -> None:
    """The path the design was actually about: a run with nobody watching."""
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "cron.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "cron-ns")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setattr("sys.argv", ["gingugu", "dream"])

    from gingugu import server as server_mod

    with pytest.raises(SystemExit) as exc:
        server_mod.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "graph:" in out and "queue:" in out


def test_dream_cli_rejects_an_unknown_namespace(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "cron.db"))
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setattr("sys.argv", ["gingugu", "dream", "no-such-namespace"])

    from gingugu import server as server_mod

    with pytest.raises(SystemExit) as exc:
        server_mod.main()
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err
