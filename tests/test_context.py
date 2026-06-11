"""Tests for the memory_context auto-context engine."""

from __future__ import annotations

import pytest

from gingugu import context
from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore

WEIGHTS = {"relevance": 0.45, "freshness": 0.25, "access": 0.10, "confidence": 0.20}


def test_context_surfaces_recent(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="recent", content="hello world")
    results = context.build_context(store.conn, namespace_id=ns_id, limit=10, weights=WEIGHTS)
    assert any(m.title == "recent" for m in results)


def test_context_task_hint_prioritizes(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="db", content="sqlite indexing tips"
    )
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="ui", content="react layout tips")
    results = context.build_context(
        store.conn, namespace_id=ns_id, task_hint="sqlite indexing", limit=10, weights=WEIGHTS
    )
    assert results[0].title == "db"


def test_context_cross_namespace_patterns(store: MemoryStore, namespaces: NamespaceManager) -> None:
    cur_ns = namespaces.get_or_create("current").id
    other_ns = namespaces.get_or_create("other").id
    store.create(
        namespace_id=other_ns,
        type=MemoryType.PATTERN,
        title="shared pattern",
        content="always pin deps",
        confidence=Confidence.VERIFIED,
    )
    results = context.build_context(store.conn, namespace_id=cur_ns, limit=10, weights=WEIGHTS)
    assert any(m.title == "shared pattern" for m in results)


def test_context_excludes_deprecated(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="dead", content="gone")
    store.update(mem.id, confidence=Confidence.DEPRECATED)
    results = context.build_context(store.conn, namespace_id=ns_id, limit=10, weights=WEIGHTS)
    assert all(m.title != "dead" for m in results)


def test_context_type_boost_applied_once(store: MemoryStore, namespaces: NamespaceManager) -> None:
    # Regression: bucket-2/3 memories used to get the +0.1 boost twice (once in
    # _score, once in the final pass), inflating architecture/decision scores.
    ns_id = namespaces.get_or_create("test-ns").id
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="plain", content="x")
    store.create(namespace_id=ns_id, type=MemoryType.ARCHITECTURE, title="arch", content="y")
    results = context.build_context(store.conn, namespace_id=ns_id, limit=10, weights=WEIGHTS)
    scores = {m.title: m.score for m in results}
    # Identical recency/confidence/access — the gap must be exactly one boost.
    assert scores["arch"] - scores["plain"] == pytest.approx(0.1, abs=1e-6)


def test_context_type_boost(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    # Same recency/confidence; architecture should outrank a plain fact via boost.
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="plain", content="x")
    store.create(namespace_id=ns_id, type=MemoryType.ARCHITECTURE, title="arch", content="y")
    results = context.build_context(store.conn, namespace_id=ns_id, limit=10, weights=WEIGHTS)
    titles = [m.title for m in results]
    assert titles.index("arch") < titles.index("plain")
