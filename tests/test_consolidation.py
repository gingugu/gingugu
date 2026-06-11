"""Tests for consolidation: merge, summarize, deduplicate strategies."""

from __future__ import annotations

import pytest

from gingugu import consolidation
from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore


def _seed(store: MemoryStore, ns_id: str) -> list[str]:
    a = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="A",
        content="alpha",
        tags=["x"],
        confidence=Confidence.INFERRED,
    )
    b = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="B",
        content="beta",
        tags=["y"],
        confidence=Confidence.VERIFIED,
    )
    return [a.id, b.id]


def test_merge_creates_new_and_deprecates(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    ids = _seed(store, ns_id)
    result = consolidation.consolidate(store, relations, memory_ids=ids, strategy="merge")
    new = store.get(result["consolidated_id"], record_access=False)
    assert "alpha" in new.content and "beta" in new.content
    assert set(new.tags) == {"x", "y"}
    # Originals deprecated and superseded.
    for oid in ids:
        assert store.get(oid, record_access=False).confidence == Confidence.DEPRECATED
    assert set(relations.related_ids(new.id)) == set(ids)


def test_merge_max_confidence(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    ids = _seed(store, ns_id)
    result = consolidation.consolidate(store, relations, memory_ids=ids, strategy="merge")
    new = store.get(result["consolidated_id"], record_access=False)
    assert new.confidence == Confidence.VERIFIED


def test_summarize_strategy(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    ids = _seed(store, ns_id)
    result = consolidation.consolidate(store, relations, memory_ids=ids, strategy="summarize")
    new = store.get(result["consolidated_id"], record_access=False)
    assert new.content.startswith("Digest of 2 memories:")


def test_deduplicate_keeps_best(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    ids = _seed(store, ns_id)  # B is verified → should be kept
    result = consolidation.consolidate(store, relations, memory_ids=ids, strategy="deduplicate")
    kept = store.get(result["consolidated_id"], record_access=False)
    assert kept.title == "B"
    assert kept.confidence == Confidence.VERIFIED
    assert result["retired"] == [ids[0]]
    assert store.get(ids[0], record_access=False).confidence == Confidence.DEPRECATED


def test_keep_originals_false_hard_deletes(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    ids = _seed(store, ns_id)
    consolidation.consolidate(
        store, relations, memory_ids=ids, strategy="merge", keep_originals=False
    )
    for oid in ids:
        assert store.get(oid, record_access=False) is None


def test_cross_namespace_rejected(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_a = namespaces.get_or_create("ns-a").id
    ns_b = namespaces.get_or_create("ns-b").id
    a = store.create(namespace_id=ns_a, type=MemoryType.FACT, title="A", content="x").id
    b = store.create(namespace_id=ns_b, type=MemoryType.FACT, title="B", content="y").id
    with pytest.raises(ValueError):
        consolidation.consolidate(store, relations, memory_ids=[a, b], strategy="merge")


def test_requires_two_ids(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    a = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="A", content="x").id
    with pytest.raises(ValueError):
        consolidation.consolidate(store, relations, memory_ids=[a], strategy="merge")
