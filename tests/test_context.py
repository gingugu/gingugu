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


def test_context_fresh_memory_survives_high_access_competition(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # Regression: a freshly-stored, never-accessed memory (the "where we left
    # off" signal) used to be evicted past the limit by older, heavily-accessed
    # memories whose composite score won the single global sort. The recency
    # quota must guarantee it surfaces.
    ns_id = namespaces.get_or_create("test-ns").id
    limit = 10
    # Build `limit` competitors and hammer their access_count so each one's
    # composite score beats a never-accessed memory.
    for i in range(limit):
        comp = store.create(
            namespace_id=ns_id, type=MemoryType.FACT, title=f"old-{i}", content="prior work"
        )
        for _ in range(20):
            store.record_accesses([comp.id])
    # The newest memory is created last (newest last_accessed) but never accessed.
    fresh = store.create(
        namespace_id=ns_id, type=MemoryType.BUG, title="traefik outage", content="root cause found"
    )
    results = context.build_context(store.conn, namespace_id=ns_id, limit=limit, weights=WEIGHTS)
    assert any(m.id == fresh.id for m in results), "fresh memory was evicted from context"


def test_context_recency_quota_does_not_starve_task_relevance(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # The guaranteed recency quota must not crowd out a strong task-hint match:
    # a memory matching the hint should still surface even when many unrelated
    # memories were touched more recently.
    ns_id = namespaces.get_or_create("test-ns").id
    target = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="db", content="sqlite indexing tips"
    )
    for i in range(10):
        store.create(
            namespace_id=ns_id,
            type=MemoryType.FACT,
            title=f"noise-{i}",
            content="unrelated chatter",
        )
    results = context.build_context(
        store.conn, namespace_id=ns_id, task_hint="sqlite indexing", limit=10, weights=WEIGHTS
    )
    assert any(m.id == target.id for m in results), "task-relevant memory was starved"
