"""Tests for the dream pass and its proposal queue.

Two properties matter more than any individual finding, and most of this file
exists to hold them:

* **The pass cannot change the brain.** It is allowed to run unattended only
  because it has no write path to ``memories`` or ``relations``, so that is
  asserted directly rather than assumed from the code's shape.
* **The pass is reproducible.** A run that returns different communities each
  time it sees the same graph is not arithmetic anybody can audit. Both
  order-dependent algorithms are pinned against re-runs.
"""

from __future__ import annotations

import pytest

from gingugu import dream
from gingugu.database import Database
from gingugu.dream import centrality, clusters
from gingugu.dream import graph as graph_mod
from gingugu.models import MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.proposals import ACCEPTED, PENDING, REJECTED, ProposalQueue, ordered_pair
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore


def _mem(store: MemoryStore, ns_id: str, title: str, content: str = "c") -> str:
    return store.create(namespace_id=ns_id, type=MemoryType.FACT, title=title, content=content).id


def _star(store: MemoryStore, relations: RelationManager, ns_id: str, spokes: int = 6):
    """A hub with ``spokes`` leaves - the smallest graph with a clear centre."""
    hub = _mem(store, ns_id, "hub")
    leaves = [_mem(store, ns_id, f"leaf-{i}") for i in range(spokes)]
    for leaf in leaves:
        relations.relate(source_id=leaf, target_id=hub, relation_type=RelationType.CAUSED_BY)
    return hub, leaves


# --- the queue ---------------------------------------------------------------


def test_stage_is_idempotent_and_refreshes_a_pending_score(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = _mem(store, ns_id, "a")
    queue = ProposalQueue(db.conn)

    assert queue.stage(pass_name="centrality", kind="core", subject_id=a, score=0.5, evidence={})
    assert queue.stage(pass_name="centrality", kind="core", subject_id=a, score=0.9, evidence={})

    pending = queue.list()
    assert len(pending) == 1, "re-running the pass must not accumulate duplicate rows"
    assert pending[0]["score"] == 0.9, "a pending score must track the graph it measures"


def test_a_decided_proposal_is_never_raised_again(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """The property that stops a cron job becoming a nagging machine."""
    ns_id = namespaces.get_or_create("test-ns").id
    a = _mem(store, ns_id, "a")
    queue = ProposalQueue(db.conn)

    queue.stage(pass_name="centrality", kind="core", subject_id=a, score=0.5, evidence={})
    proposal_id = queue.list()[0]["id"]
    queue.decide(proposal_id, REJECTED)

    # The same computation, run again on the same graph, reaches the same
    # conclusion. The answer is already on file.
    assert not queue.stage(
        pass_name="centrality", kind="core", subject_id=a, score=0.91, evidence={"new": True}
    )
    assert queue.list(status=PENDING) == []
    settled = queue.list(status=REJECTED)[0]
    assert settled["score"] == 0.5, "a decided row is a record, not a slot to overwrite"
    assert settled["evidence"] == {}


def test_unordered_pairs_collapse_to_one_proposal(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    queue = ProposalQueue(db.conn)

    for source, target in ((a, b), (b, a)):
        subject, obj = ordered_pair(source, target)
        queue.stage(
            pass_name="orphans",
            kind="edge",
            subject_id=subject,
            object_id=obj,
            score=0.8,
            evidence={},
        )
    assert len(queue.list()) == 1, "closeness has no direction; one finding, one row"


def test_forgetting_a_memory_cascades_to_its_proposals(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = _mem(store, ns_id, "a")
    queue = ProposalQueue(db.conn)
    queue.stage(pass_name="centrality", kind="core", subject_id=a, score=0.5, evidence={})

    db.conn.execute("DELETE FROM memories WHERE id = ?", (a,))
    db.conn.commit()
    assert queue.list() == [], "a proposal about a memory that no longer exists is noise"


def test_stage_rejects_a_self_pair_and_an_unknown_kind(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = _mem(store, ns_id, "a")
    queue = ProposalQueue(db.conn)
    with pytest.raises(ValueError):
        queue.stage(
            pass_name="orphans", kind="edge", subject_id=a, object_id=a, score=1.0, evidence={}
        )
    with pytest.raises(ValueError):
        queue.stage(pass_name="x", kind="nonsense", subject_id=a, score=1.0, evidence={})


# --- centrality --------------------------------------------------------------


def test_pagerank_ranks_the_hub_above_its_leaves(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    hub, leaves = _star(store, relations, ns_id)

    ranks = centrality.pagerank(graph_mod.load(db.conn))
    assert ranks[hub] > max(ranks[leaf] for leaf in leaves)
    assert sum(ranks.values()) == pytest.approx(1.0), "rank must stay a distribution"


def test_centrality_skips_what_is_already_pinned(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """The finding is the memory nobody nominated, not the one already in."""
    ns_id = namespaces.get_or_create("test-ns").id
    hub, _ = _star(store, relations, ns_id)

    graph = graph_mod.load(db.conn)
    assert any(f["subject_id"] == hub for f in centrality.find(db.conn, graph))

    store.update(hub, pinned=True)
    assert not any(f["subject_id"] == hub for f in centrality.find(db.conn, graph))


def test_centrality_proposes_nothing_on_a_flat_graph(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """No edges means no memory is central, and the honest answer is silence."""
    ns_id = namespaces.get_or_create("test-ns").id
    for i in range(8):
        _mem(store, ns_id, f"m-{i}")
    assert centrality.find(db.conn, graph_mod.load(db.conn)) == []


# --- clusters ----------------------------------------------------------------


def test_label_propagation_separates_two_communities(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    left = [_mem(store, ns_id, f"L{i}") for i in range(4)]
    right = [_mem(store, ns_id, f"R{i}") for i in range(4)]
    for group in (left, right):
        for i, source in enumerate(group):
            for target in group[i + 1 :]:
                relations.relate(
                    source_id=source, target_id=target, relation_type=RelationType.RELATED_TO
                )

    labels = clusters.propagate(graph_mod.load(db.conn))
    assert len({labels[m] for m in left}) == 1
    assert len({labels[m] for m in right}) == 1
    assert labels[left[0]] != labels[right[0]], "two cliques are two communities"


def test_clustering_is_reproducible(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """Published label propagation randomises node order; ours must not.

    A pass whose findings change between identical runs cannot be audited, and
    auditability is the only reason it is allowed to run unattended.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    nodes = [_mem(store, ns_id, f"n{i}") for i in range(10)]
    for i in range(0, 9):
        relations.relate(
            source_id=nodes[i], target_id=nodes[i + 1], relation_type=RelationType.CHILD_OF
        )

    graph = graph_mod.load(db.conn)
    first = clusters.find(graph)
    for _ in range(3):
        assert clusters.find(graph_mod.load(db.conn)) == first


def test_a_pair_is_not_a_community(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.SUPERSEDES)
    assert clusters.find(graph_mod.load(db.conn)) == []


# --- the graph ---------------------------------------------------------------


def test_namespace_scope_drops_half_dangling_edges(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """Centrality inside a namespace must not be inflated from outside it."""
    here = namespaces.get_or_create("here").id
    there = namespaces.get_or_create("there").id
    a = _mem(store, here, "a")
    b = _mem(store, there, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.CAUSED_BY)

    scoped = graph_mod.load(db.conn, namespace_id=here)
    assert scoped.nodes == [a]
    assert scoped.edge_count == 0
    assert scoped.orphans == [a]

    assert graph_mod.load(db.conn).edge_count == 1


# --- the run -----------------------------------------------------------------


def test_a_run_writes_only_to_the_queue(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """The property that makes the pass safe to schedule.

    Not a proxy for it: the memories and relations tables are snapshotted whole
    and compared after a run that stages real findings.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    _star(store, relations, ns_id)
    for i in range(3):
        _mem(store, ns_id, f"loner-{i}", content=f"unrelated content {i}")

    def snapshot() -> tuple:
        return (
            db.conn.execute("SELECT * FROM memories ORDER BY id").fetchall(),
            db.conn.execute("SELECT * FROM relations ORDER BY id").fetchall(),
        )

    before_memories, before_relations = snapshot()
    report = dream.run(db.conn)
    after_memories, after_relations = snapshot()

    assert report["queue"]["pending"] > 0, "the run must actually have found something"
    assert [tuple(r) for r in after_memories] == [tuple(r) for r in before_memories]
    assert [tuple(r) for r in after_relations] == [tuple(r) for r in before_relations]


def test_a_failing_pass_does_not_lose_the_others(
    db: Database,
    store: MemoryStore,
    namespaces: NamespaceManager,
    relations: RelationManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduled work with nobody watching: two good passes beat an abort."""
    ns_id = namespaces.get_or_create("test-ns").id
    _star(store, relations, ns_id)

    def boom(*args, **kwargs):
        raise RuntimeError("pass exploded")

    monkeypatch.setattr("gingugu.dream.clusters.find", boom)
    report = dream.run(db.conn)

    assert report["passes"]["clusters"]["error"] is True
    assert report["passes"]["centrality"]["staged"] > 0


def test_a_second_run_stages_nothing_new(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    _star(store, relations, ns_id)

    first = dream.run(db.conn)
    second = dream.run(db.conn)
    assert second["queue"]["pending"] == first["queue"]["pending"]


def test_accepting_a_proposal_is_what_writes_the_edge(
    db: Database, store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """Deciding is bookkeeping; applying is a separate, deliberate act."""
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    queue = ProposalQueue(db.conn)
    subject, obj = ordered_pair(a, b)
    queue.stage(
        pass_name="orphans",
        kind="edge",
        subject_id=subject,
        object_id=obj,
        score=0.8,
        evidence={"relation_type": None},
    )
    proposal_id = queue.list()[0]["id"]

    decided = queue.decide(proposal_id, ACCEPTED)
    assert decided["status"] == ACCEPTED
    assert decided["decided_at"] is not None
    assert db.conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 0
