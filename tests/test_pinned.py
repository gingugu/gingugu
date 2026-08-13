"""Tests for the pinned tier: memories that always load, exempt from ranking.

The tier exists because ranking answers "what is most relevant to this task?"
and cannot answer "what must never be missing?". These tests hold that line:
the interesting cases are the ones where ranking *would* have dropped the
memory and the pin overrides it.
"""

from __future__ import annotations

from gingugu import context
from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore

WEIGHTS = {"relevance": 0.45, "freshness": 0.25, "access": 0.10, "confidence": 0.20}


def _ctx(store: MemoryStore, ns_id: str, **kw):
    return context.build_context(store.conn, namespace_id=ns_id, weights=WEIGHTS, **kw)


def test_new_memories_are_not_pinned(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c")
    assert mem.pinned is False
    assert store.count_pinned(ns_id) == 0


def test_pin_round_trips(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c")

    store.update(mem.id, pinned=True)
    assert store.get(mem.id, record_access=False).pinned is True
    assert store.count_pinned(ns_id) == 1

    store.update(mem.id, pinned=False)
    assert store.get(mem.id, record_access=False).pinned is False
    assert store.count_pinned(ns_id) == 0


def test_pin_is_untouched_by_unrelated_updates(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """``pinned=None`` means "leave alone", matching every other update field."""
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c")
    store.update(mem.id, pinned=True)

    store.update(mem.id, title="new title")
    assert store.get(mem.id, record_access=False).pinned is True


def test_pinning_does_not_advance_last_confirmed(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """A pin is a retrieval decision, not a claim that the content is still true.

    If pinning advanced the freshness clock it would silently suppress the
    review hints that tell you a memory has gone stale — the pinned memories
    are exactly the ones where that would hurt most.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="c")
    before = store.get(mem.id, record_access=False).last_confirmed

    store.update(mem.id, pinned=True)
    assert store.get(mem.id, record_access=False).last_confirmed == before


def test_pinned_survives_when_ranking_would_evict_it(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """The whole point of the tier, in one test.

    One pinned memory that is old, never accessed, and topically unrelated to
    the task hint — every signal the ranker uses says "drop this" — plus enough
    competing memories to fill the limit several times over.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    rule = store.create(
        namespace_id=ns_id,
        type=MemoryType.PREFERENCE,
        title="never deploy on friday",
        content="hard rule about release windows",
    )
    store.update(rule.id, pinned=True)

    for i in range(20):
        store.create(
            namespace_id=ns_id,
            type=MemoryType.FACT,
            title=f"sqlite note {i}",
            content="sqlite indexing and query planning notes",
        )

    results = _ctx(store, ns_id, task_hint="sqlite indexing", limit=5)
    assert rule.id in {m.id for m in results}


def test_pinned_come_first(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    for i in range(5):
        store.create(
            namespace_id=ns_id, type=MemoryType.FACT, title=f"noise {i}", content="sqlite notes"
        )
    rule = store.create(
        namespace_id=ns_id, type=MemoryType.PREFERENCE, title="the rule", content="unrelated"
    )
    store.update(rule.id, pinned=True)

    results = _ctx(store, ns_id, task_hint="sqlite", limit=5)
    assert results[0].id == rule.id


def test_pinned_are_additive_to_limit(store: MemoryStore, namespaces: NamespaceManager) -> None:
    """Pins are a floor, not a share of ``limit``.

    Were they a share, a full pin tier would truncate the ranked set to nothing
    — or truncate itself, which is the failure the tier exists to prevent.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    for i in range(10):
        store.create(namespace_id=ns_id, type=MemoryType.FACT, title=f"noise {i}", content="body")
    pins = [
        store.create(
            namespace_id=ns_id, type=MemoryType.PREFERENCE, title=f"rule {i}", content="body"
        )
        for i in range(3)
    ]
    for p in pins:
        store.update(p.id, pinned=True)

    results = _ctx(store, ns_id, limit=5)
    assert len(results) == 8  # 3 pinned + 5 ranked
    assert {p.id for p in pins} <= {m.id for m in results}


def test_pin_does_not_also_consume_a_ranked_slot(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    """A pinned memory that would also win on relevance must appear exactly once."""
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="sqlite indexing",
        content="sqlite indexing and query planning",
    )
    store.update(mem.id, pinned=True)
    for i in range(5):
        store.create(
            namespace_id=ns_id, type=MemoryType.FACT, title=f"other {i}", content="other body"
        )

    results = _ctx(store, ns_id, task_hint="sqlite indexing", limit=5)
    assert [m.id for m in results].count(mem.id) == 1


def test_deprecated_pins_are_ignored(store: MemoryStore, namespaces: NamespaceManager) -> None:
    """Deprecation beats a pin: "no longer true" outranks "never let me miss this"."""
    ns_id = namespaces.get_or_create("test-ns").id
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="old rule", content="body")
    store.update(mem.id, pinned=True, confidence=Confidence.DEPRECATED)

    results = _ctx(store, ns_id, limit=5)
    assert mem.id not in {m.id for m in results}
    assert store.count_pinned(ns_id) == 0


def test_pins_are_namespace_scoped(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_a = namespaces.get_or_create("ns-a").id
    ns_b = namespaces.get_or_create("ns-b").id
    mem = store.create(namespace_id=ns_a, type=MemoryType.FACT, title="a rule", content="body")
    store.update(mem.id, pinned=True)

    assert store.count_pinned(ns_b) == 0
    assert mem.id not in {m.id for m in _ctx(store, ns_b, limit=5)}


def test_hard_cap_bounds_what_loads(store: MemoryStore, namespaces: NamespaceManager) -> None:
    """Storage takes any number of pins; the context load is what's bounded.

    The cap is enforced at the tool surface, so a store written to directly (or
    an import) can exceed it. Context must stay bounded regardless.
    """
    ns_id = namespaces.get_or_create("test-ns").id
    for i in range(context.PINNED_HARD_CAP + 5):
        mem = store.create(
            namespace_id=ns_id, type=MemoryType.PREFERENCE, title=f"rule {i}", content="body"
        )
        store.update(mem.id, pinned=True)

    results = _ctx(store, ns_id, limit=5)
    assert sum(1 for m in results if m.pinned) == context.PINNED_HARD_CAP
