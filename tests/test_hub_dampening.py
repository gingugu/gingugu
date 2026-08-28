"""Tests for hub-dampened 1-hop traversal (RelationManager.dampened_neighbour_ids).

One highly-connected "generic hub" memory must not drag its whole
neighbourhood into every recall: each seed contributes at most
``per_seed`` neighbours (most trusted, most specific first) and the whole
set is capped at ``total``.

Selection order is confidence, then relation weight, then low degree, then
recency, then id. The relation-weight term is what keeps a ``related_to``
majority from taking every slot on a memory that also carries directional
edges; confidence stays above it because ``supersedes`` habitually points at
the deprecated memory it replaced.
"""

from __future__ import annotations

import pytest

from gingugu.models import RELATION_WEIGHT, Confidence, MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore


def _mem(store, ns_id, title, confidence=Confidence.VERIFIED):
    return store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title=title,
        content=f"content for {title}",
        confidence=confidence,
    )


def test_per_seed_budget_caps_a_hub(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    hub = _mem(store, ns_id, "hub")
    spokes = [_mem(store, ns_id, f"spoke{i}") for i in range(8)]
    for s in spokes:
        relations.relate(source_id=hub.id, target_id=s.id, relation_type=RelationType.RELATED_TO)

    out = relations.dampened_neighbour_ids([hub.id], per_seed=3, total=10)
    assert len(out) == 3
    assert hub.id not in out


def test_confidence_outranks_recency_and_degree_breaks_ties(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    seed = _mem(store, ns_id, "seed")
    inferred = _mem(store, ns_id, "inferred-neighbour", confidence=Confidence.INFERRED)
    verified_focused = _mem(store, ns_id, "verified-focused")
    verified_hub = _mem(store, ns_id, "verified-hub")
    # Make verified_hub a high-degree hub: connect it to several others.
    for i in range(5):
        other = _mem(store, ns_id, f"hub-satellite{i}")
        relations.relate(
            source_id=verified_hub.id, target_id=other.id, relation_type=RelationType.RELATED_TO
        )
    for n in (inferred, verified_focused, verified_hub):
        relations.relate(source_id=seed.id, target_id=n.id, relation_type=RelationType.RELATED_TO)

    out = relations.dampened_neighbour_ids([seed.id], per_seed=2, total=10)
    # verified beats inferred; at equal confidence the low-degree (focused)
    # neighbour beats the hub.
    assert out[0] == verified_focused.id
    assert out[1] == verified_hub.id
    assert inferred.id not in out


def test_total_cap_fills_in_seed_order(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    seed_a = _mem(store, ns_id, "seed-a")
    seed_b = _mem(store, ns_id, "seed-b")
    a_neighbours = [_mem(store, ns_id, f"a{i}") for i in range(3)]
    b_neighbours = [_mem(store, ns_id, f"b{i}") for i in range(3)]
    for n in a_neighbours:
        relations.relate(source_id=seed_a.id, target_id=n.id, relation_type=RelationType.RELATED_TO)
    for n in b_neighbours:
        relations.relate(source_id=seed_b.id, target_id=n.id, relation_type=RelationType.RELATED_TO)

    out = relations.dampened_neighbour_ids([seed_a.id, seed_b.id], per_seed=3, total=4)
    assert len(out) == 4
    # Seed A's cluster fills first (seeds arrive relevance-ranked).
    assert set(out[:3]) == {n.id for n in a_neighbours}
    assert out[3] in {n.id for n in b_neighbours}


def test_seeds_and_duplicates_excluded(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    seed_a = _mem(store, ns_id, "seed-a")
    seed_b = _mem(store, ns_id, "seed-b")
    shared = _mem(store, ns_id, "shared-neighbour")
    rel = RelationType.RELATED_TO
    relations.relate(source_id=seed_a.id, target_id=seed_b.id, relation_type=rel)
    relations.relate(source_id=seed_a.id, target_id=shared.id, relation_type=rel)
    relations.relate(source_id=seed_b.id, target_id=shared.id, relation_type=rel)

    out = relations.dampened_neighbour_ids([seed_a.id, seed_b.id])
    assert out == [shared.id]  # seeds excluded, shared neighbour appears once


def test_directional_edge_outranks_related_to_for_a_slot(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """The defect this term exists to fix, in its exact real-world shape.

    The directional neighbour is created FIRST, so it carries the OLDEST
    timestamp and loses the recency tiebreak. Under the type-blind sort the
    three newer ``related_to`` neighbours took all three slots and the
    ``supersedes`` edge never fired at all.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    seed = _mem(store, ns_id, "seed")
    superseded = _mem(store, ns_id, "the-memory-this-one-replaced")
    relations.relate(
        source_id=seed.id, target_id=superseded.id, relation_type=RelationType.SUPERSEDES
    )
    chatter = [_mem(store, ns_id, f"same-topic{i}") for i in range(3)]
    for n in chatter:
        relations.relate(source_id=seed.id, target_id=n.id, relation_type=RelationType.RELATED_TO)

    out = relations.dampened_neighbour_ids([seed.id], per_seed=3, total=10)
    assert len(out) == 3
    assert out[0] == superseded.id
    # One low-signal neighbour is pushed out of the budget, not all of them.
    assert len([i for i in out if i in {n.id for n in chatter}]) == 2


def test_confidence_still_outranks_relation_type(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """Type must not promote a dead memory over a live one.

    ``supersedes`` points at what was replaced, which is routinely deprecated.
    Weighting type above confidence would make every such edge a channel for
    surfacing exactly the memory the graph records as no longer true.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    seed = _mem(store, ns_id, "seed")
    dead = _mem(store, ns_id, "deprecated-predecessor", confidence=Confidence.DEPRECATED)
    live = _mem(store, ns_id, "live-but-merely-adjacent")
    relations.relate(source_id=seed.id, target_id=dead.id, relation_type=RelationType.SUPERSEDES)
    relations.relate(source_id=seed.id, target_id=live.id, relation_type=RelationType.RELATED_TO)

    out = relations.dampened_neighbour_ids([seed.id], per_seed=1, total=10)
    assert out == [live.id]


def test_multi_edge_neighbour_takes_one_slot_and_its_strongest_edge(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """Two memories may be joined by several edges; that is one neighbour.

    The pre-existing ``seen`` guard was evaluated while candidates were built,
    so it could not catch a duplicate arising within a single seed: such a
    neighbour spent one slot per edge and was emitted once per edge. Grouping
    per neighbour fixes both, and the pair is scored by its strongest edge.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    seed = _mem(store, ns_id, "seed")
    both = _mem(store, ns_id, "joined-by-two-edges")
    others = [_mem(store, ns_id, f"other{i}") for i in range(2)]
    relations.relate(source_id=seed.id, target_id=both.id, relation_type=RelationType.RELATED_TO)
    relations.relate(source_id=seed.id, target_id=both.id, relation_type=RelationType.CAUSED_BY)
    for n in others:
        relations.relate(source_id=seed.id, target_id=n.id, relation_type=RelationType.RELATED_TO)

    out = relations.dampened_neighbour_ids([seed.id], per_seed=3, total=10)
    assert out.count(both.id) == 1
    assert len(out) == len(set(out)) == 3
    # Scored on its strongest edge (caused_by), so it leads despite the
    # related_to edge it also carries.
    assert out[0] == both.id
    assert {n.id for n in others} <= set(out)


def test_relation_weight_covers_every_relation_type() -> None:
    """A new edge type must not silently default to the low-signal tier.

    ``RELATION_WEIGHT.get(..., 0)`` is a safe fallback for junk data, not a
    place to leave a real type. Adding one to the enum is a decision about how
    retrieval should rank it, and this fails until that decision is recorded.
    """
    assert set(RELATION_WEIGHT) == {t.value for t in RelationType}


@pytest.mark.parametrize(
    "relation_type", [t for t in RelationType if t is not RelationType.RELATED_TO]
)
def test_every_directional_type_beats_the_fallback(
    store: MemoryStore,
    namespaces: NamespaceManager,
    relations: RelationManager,
    relation_type: RelationType,
) -> None:
    """``related_to`` is the only low-signal tier - no directional type is."""
    ns_id = namespaces.get_or_create("test-ns").id
    seed = _mem(store, ns_id, "seed")
    directional = _mem(store, ns_id, "directional")
    fallback = _mem(store, ns_id, "fallback")
    relations.relate(source_id=seed.id, target_id=directional.id, relation_type=relation_type)
    relations.relate(
        source_id=seed.id, target_id=fallback.id, relation_type=RelationType.RELATED_TO
    )

    assert relations.dampened_neighbour_ids([seed.id], per_seed=1, total=10) == [directional.id]


def test_deterministic_output(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    seed = _mem(store, ns_id, "seed")
    for i in range(6):
        n = _mem(store, ns_id, f"n{i}")
        relations.relate(source_id=seed.id, target_id=n.id, relation_type=RelationType.RELATED_TO)
    first = relations.dampened_neighbour_ids([seed.id], per_seed=4, total=10)
    second = relations.dampened_neighbour_ids([seed.id], per_seed=4, total=10)
    assert first == second
    assert len(first) == 4
