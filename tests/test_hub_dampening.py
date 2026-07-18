"""Tests for hub-dampened 1-hop traversal (RelationManager.dampened_neighbour_ids).

One highly-connected "generic hub" memory must not drag its whole
neighbourhood into every recall: each seed contributes at most
``per_seed`` neighbours (most trusted, most specific first) and the whole
set is capped at ``total``.
"""

from __future__ import annotations

from gingugu.models import Confidence, MemoryType, RelationType
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
