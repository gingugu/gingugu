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


def test_relations_cascade_on_memory_delete(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a, b = _mem(store, ns_id, "a"), _mem(store, ns_id, "b")
    relations.relate(source_id=a, target_id=b, relation_type=RelationType.RELATED_TO)
    store.delete(b)
    assert relations.get_relations(a) == []
