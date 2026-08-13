"""Tests for relation-graph health metrics in ``memory_stats``."""

from __future__ import annotations

from gingugu.graph_stats import compute_graph
from gingugu.models import MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import SPREAD_PER_SEED, RelationManager
from gingugu.storage import MemoryStore


def _mk(store: MemoryStore, ns_id: str, title: str):
    return store.create(namespace_id=ns_id, type=MemoryType.FACT, title=title, content=title)


def test_empty_graph_reports_zeros_not_division_errors(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """An empty store must not blow up the ratio math."""
    namespaces.get_or_create("test-ns")
    graph = compute_graph(store.conn)
    assert graph["edges"] == 0
    assert graph["edges_per_memory"] == 0.0
    assert graph["orphan_ratio"] == 0.0
    assert graph["high_signal_ratio"] == 0.0


def test_counts_edges_and_types(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b, c = (_mk(store, ns_id, t) for t in ("a", "b", "c"))
    relations.relate(source_id=a.id, target_id=b.id, relation_type=RelationType.SUPERSEDES)
    relations.relate(source_id=b.id, target_id=c.id, relation_type=RelationType.RELATED_TO)

    graph = compute_graph(store.conn)
    assert graph["edges"] == 2
    assert graph["by_relation_type"] == {"supersedes": 1, "related_to": 1}


def test_high_signal_ratio_excludes_related_to(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """``related_to`` is the fallback edge and must not count as signal.

    A graph of pure ``related_to`` encodes nothing the text/semantic index does
    not already infer, so a high edge count with a low signal ratio is exactly
    the state worth surfacing.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    a, b, c, d = (_mk(store, ns_id, t) for t in ("a", "b", "c", "d"))
    relations.relate(source_id=a.id, target_id=b.id, relation_type=RelationType.RELATED_TO)
    relations.relate(source_id=b.id, target_id=c.id, relation_type=RelationType.RELATED_TO)
    relations.relate(source_id=c.id, target_id=d.id, relation_type=RelationType.RELATED_TO)
    relations.relate(source_id=a.id, target_id=d.id, relation_type=RelationType.CAUSED_BY)

    graph = compute_graph(store.conn)
    assert graph["edges"] == 4
    assert graph["high_signal_edges"] == 1
    assert graph["high_signal_ratio"] == 0.25


def test_orphans_counted_from_either_edge_direction(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """Being a relation's *target* is enough to not be an orphan.

    Counting only outbound edges would report a memory as unreachable when
    spreading activation can in fact reach it.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    a, b, lonely = (_mk(store, ns_id, t) for t in ("a", "b", "lonely"))
    relations.relate(source_id=a.id, target_id=b.id, relation_type=RelationType.CAUSED_BY)

    graph = compute_graph(store.conn)
    assert graph["orphans"] == 1
    assert graph["orphan_ratio"] == round(1 / 3, 3)
    assert lonely.id and b.id  # b is only ever a target, and is not an orphan


def test_over_spread_cap_flags_unreachable_edges(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """Edges past ``SPREAD_PER_SEED`` on one memory can never fire."""
    ns_id = namespaces.get_or_create("test-ns").id
    hub = _mk(store, ns_id, "hub")
    for i in range(SPREAD_PER_SEED + 2):
        leaf = _mk(store, ns_id, f"leaf {i}")
        relations.relate(source_id=hub.id, target_id=leaf.id, relation_type=RelationType.PARENT_OF)

    graph = compute_graph(store.conn)
    assert graph["over_spread_cap"] == 1
    assert graph["spread_per_seed"] == SPREAD_PER_SEED


def test_namespace_scope_counts_cross_namespace_edges(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """A cross-namespace edge belongs to both namespaces it touches.

    Counting by source only would hide inbound edges entirely, making a
    namespace look less connected than it is.
    """
    ns_a = namespaces.get_or_create("ns-a").id
    ns_b = namespaces.get_or_create("ns-b").id
    a = _mk(store, ns_a, "in a")
    b = _mk(store, ns_b, "in b")
    relations.relate(source_id=a.id, target_id=b.id, relation_type=RelationType.CAUSED_BY)

    assert compute_graph(store.conn, namespace_id=ns_a)["edges"] == 1
    assert compute_graph(store.conn, namespace_id=ns_b)["edges"] == 1
    assert compute_graph(store.conn)["edges"] == 1


def test_graph_block_is_exposed_in_stats(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    from gingugu.stats import compute_stats

    ns_id = namespaces.get_or_create("test-ns").id
    a, b = (_mk(store, ns_id, t) for t in ("a", "b"))
    relations.relate(source_id=a.id, target_id=b.id, relation_type=RelationType.SUPERSEDES)

    stats = compute_stats(store.conn)
    assert stats["graph"]["edges"] == 1
    assert stats["graph"]["high_signal_ratio"] == 1.0
