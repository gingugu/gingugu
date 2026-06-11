"""Tests for NamespaceManager CRUD and delete guards."""

from __future__ import annotations

import pytest

from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


def test_get_returns_none_for_missing(namespaces: NamespaceManager) -> None:
    assert namespaces.get("ghost") is None


def test_update_path_and_description(namespaces: NamespaceManager) -> None:
    namespaces.get_or_create("proj")
    updated = namespaces.update("proj", path="/tmp/proj", description="A project")
    assert updated.path == "/tmp/proj"
    assert updated.description == "A project"


def test_update_preserves_unset_fields(namespaces: NamespaceManager) -> None:
    namespaces.get_or_create("proj", path="/orig", description="orig")
    updated = namespaces.update("proj", description="new")
    assert updated.path == "/orig"
    assert updated.description == "new"


def test_update_missing_returns_none(namespaces: NamespaceManager) -> None:
    assert namespaces.update("ghost", path="/x") is None


def test_delete_empty_namespace(namespaces: NamespaceManager) -> None:
    namespaces.get_or_create("temp")
    assert namespaces.delete("temp") == 0
    assert namespaces.get("temp") is None


def test_delete_default_rejected(namespaces: NamespaceManager) -> None:
    namespaces.get_or_create("default")
    with pytest.raises(ValueError, match="default"):
        namespaces.delete("default")


def test_delete_missing_rejected(namespaces: NamespaceManager) -> None:
    with pytest.raises(ValueError, match="not found"):
        namespaces.delete("ghost")


def test_delete_nonempty_requires_cascade(namespaces: NamespaceManager, store: MemoryStore) -> None:
    ns = namespaces.get_or_create("full")
    store.create(namespace_id=ns.id, type=MemoryType.FACT, title="a", content="x")
    with pytest.raises(ValueError, match="cascade"):
        namespaces.delete("full")
    assert namespaces.get("full") is not None  # untouched


def test_delete_cascade_removes_memories(namespaces: NamespaceManager, store: MemoryStore) -> None:
    ns = namespaces.get_or_create("full")
    m = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="a", content="x")
    removed = namespaces.delete("full", cascade=True)
    assert removed == 1
    assert namespaces.get("full") is None
    assert store.get(m.id, record_access=False) is None


def test_count_memories(namespaces: NamespaceManager, store: MemoryStore) -> None:
    ns = namespaces.get_or_create("counted")
    store.create(namespace_id=ns.id, type=MemoryType.FACT, title="a", content="x")
    store.create(namespace_id=ns.id, type=MemoryType.BUG, title="b", content="y")
    assert namespaces.count_memories(ns.id) == 2
