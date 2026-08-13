"""Tests for RelationManager: relate, traversal, validation, delete."""

from __future__ import annotations

import pytest

from gingugu.models import MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore


def _mem(store: MemoryStore, ns_id: str, title: str) -> str:
    return store.create(namespace_id=ns_id, type=MemoryType.FACT, title=title, content="c").id


def test_relate_and_get(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.SUPERSEDES)
    rels_a = relations.get_relations(a)
    assert len(rels_a) == 1
    assert rels_a[0]["direction"] == "outgoing"
    assert rels_a[0]["other_id"] == b
    rels_b = relations.get_relations(b)
    assert rels_b[0]["direction"] == "incoming"


def test_relate_is_idempotent(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)
    assert len(relations.get_relations(a)) == 1


def test_related_ids_both_directions(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b, c = _mem(store, ns_id, "a"), _mem(store, ns_id, "b"), _mem(store, ns_id, "c")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)
    relations.relate(source_id=c, target_id=a, relation_type=RelationType.CAUSED_BY)
    assert set(relations.related_ids(a)) == {b, c}


def test_relate_self_rejected(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = _mem(store, ns_id, "a")
    with pytest.raises(ValueError):
        relations.relate(source_id=a, target_id=a, relation_type=RelationType.RELATED_TO)


def test_relate_missing_memory_rejected(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = _mem(store, ns_id, "a")
    with pytest.raises(ValueError):
        relations.relate(source_id=a, target_id="ghost", relation_type=RelationType.RELATED_TO)


def test_delete_relation(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)
    assert relations.delete_relation(
        source_id=a, target_id=b, relation_type=RelationType.RELATED_TO
    )
    assert relations.get_relations(a) == []


def test_retype_reports_unchanged_when_the_type_already_matches(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)
    assert (
        relations.retype_relation(
            source_id=a,
            target_id=b,
            old_type=RelationType.RELATED_TO,
            new_type=RelationType.RELATED_TO,
        )
        == "unchanged"
    )
    assert len(relations.get_relations(a)) == 1


def test_retype_of_absent_edge_is_not_found_even_when_types_match(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """A no-op retype must not report success on an edge that was never there."""
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    assert (
        relations.retype_relation(
            source_id=a,
            target_id=b,
            old_type=RelationType.RELATED_TO,
            new_type=RelationType.RELATED_TO,
        )
        == "not_found"
    )


def test_retype_does_not_touch_the_reverse_direction(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """Edges are directed; a repair names one direction and leaves the other."""
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)
    relations.relate(source_id=b, target_id=a, relation_type=RelationType.RELATED_TO)

    relations.retype_relation(
        source_id=a,
        target_id=b,
        old_type=RelationType.RELATED_TO,
        new_type=RelationType.CAUSED_BY,
    )
    types = {(r["direction"], r["relation_type"]) for r in relations.get_relations(a)}
    assert types == {("outgoing", "caused_by"), ("incoming", "related_to")}


def test_delete_edges_removes_every_type_in_one_direction(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.CAUSED_BY)
    relations.relate(source_id=b, target_id=a, relation_type=RelationType.SUPERSEDES)

    removed = relations.delete_edges(source_id=a, target_id=b)
    assert set(removed) == {"related_to", "caused_by"}
    assert [r["relation_type"] for r in relations.get_relations(a)] == ["supersedes"]


def test_list_edges_spans_namespaces_from_either_endpoint(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    """Relations legitimately cross namespaces; a source-only filter would hide
    half of them, so the filter matches on either end."""
    left = namespaces.get_or_create("left").id
    right = namespaces.get_or_create("right").id
    a, b = _mem(store, left, "a"), _mem(store, right, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)

    for ns_id in (left, right):
        result = relations.list_edges(namespace_id=ns_id)
        assert result["total"] == 1
    edge = relations.list_edges(namespace_id=right)["edges"][0]
    assert edge["source_namespace"] == "left"
    assert edge["target_namespace"] == "right"


def test_relations_cascade_on_memory_delete(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)
    store.delete(b)
    assert relations.get_relations(a) == []
