"""Unit tests for the ``_suggest_relations`` hint helper.

The integration suite (``test_integration.py``) pins the structural contracts:
``suggested_relations`` is a list, disjoint from ``similar_memories``, skippable
via ``relation_check=False``, and excludes already-related memories. What it
does *not* assert is the positive path — that a genuine candidate actually gets
surfaced — because real hybrid-search scores aren't deterministic enough to
pin a single id.

These unit tests close that gap by mocking ``search_mod.search`` to return
memories with controlled scores, then asserting exactly which candidates clear
the relation threshold and the skip set.
"""

from __future__ import annotations

from gingugu.config import Config
from gingugu.database import Database
from gingugu.handlers import ServerContext, helpers
from gingugu.handlers.helpers import (
    _RELATION_LIMIT,
    _RELATION_MIN_SCORE,
    _suggest_relations,
)
from gingugu.models import MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore


def _ctx(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config
) -> ServerContext:
    return ServerContext(config=config, store=store, namespaces=namespaces, conn=db.conn)


def _mem(store: MemoryStore, ns_id: str, title: str, score: float):
    """Create a real memory, then stamp it with a controlled search score."""
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title=title, content="c")
    mem.score = score
    return mem


def _patch_search(monkeypatch, hits: list) -> None:
    monkeypatch.setattr(helpers.search_mod, "search", lambda *a, **k: list(hits))


def test_candidate_above_threshold_is_suggested(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    hit = _mem(store, ns_id, "candidate", score=_RELATION_MIN_SCORE + 0.1)
    _patch_search(monkeypatch, [hit])

    ctx = _ctx(db, store, namespaces, config)
    out = _suggest_relations(ctx, memory_id=None, namespace_id=ns_id, title="t", content="c")

    assert [m["id"] for m in out] == [hit.id]
    assert out[0]["title"] == "candidate"


def test_candidate_below_threshold_is_dropped(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    weak = _mem(store, ns_id, "weak", score=_RELATION_MIN_SCORE - 0.05)
    _patch_search(monkeypatch, [weak])

    ctx = _ctx(db, store, namespaces, config)
    out = _suggest_relations(ctx, memory_id=None, namespace_id=ns_id, title="t", content="c")

    assert out == []


def test_excludes_self_and_explicit_exclude_ids(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    above = _RELATION_MIN_SCORE + 0.2
    me = _mem(store, ns_id, "self", score=above)
    dup = _mem(store, ns_id, "already-surfaced-as-similar", score=above)
    keep = _mem(store, ns_id, "real-candidate", score=above)
    # search returns all three, but self and the exclude-id must be filtered out.
    _patch_search(monkeypatch, [me, dup, keep])

    ctx = _ctx(db, store, namespaces, config)
    out = _suggest_relations(
        ctx,
        memory_id=me.id,
        namespace_id=ns_id,
        title="t",
        content="c",
        exclude_ids={dup.id},
    )

    assert [m["id"] for m in out] == [keep.id]


def test_excludes_already_related_memory(
    db: Database,
    store: MemoryStore,
    namespaces: NamespaceManager,
    relations: RelationManager,
    config: Config,
    monkeypatch,
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    above = _RELATION_MIN_SCORE + 0.2
    source = _mem(store, ns_id, "source", score=above)
    linked = _mem(store, ns_id, "already-linked", score=above)
    fresh = _mem(store, ns_id, "not-yet-linked", score=above)
    relations.relate(
        source_id=source.id, target_id=linked.id, relation_type=RelationType.RELATED_TO
    )
    # search surfaces both, but the existing edge to `linked` must exclude it.
    _patch_search(monkeypatch, [linked, fresh])

    ctx = _ctx(db, store, namespaces, config)
    out = _suggest_relations(ctx, memory_id=source.id, namespace_id=ns_id, title="t", content="c")

    assert [m["id"] for m in out] == [fresh.id]


def test_respects_relation_limit(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    above = _RELATION_MIN_SCORE + 0.2
    hits = [_mem(store, ns_id, f"cand-{i}", score=above) for i in range(_RELATION_LIMIT + 2)]
    _patch_search(monkeypatch, hits)

    ctx = _ctx(db, store, namespaces, config)
    out = _suggest_relations(ctx, memory_id=None, namespace_id=ns_id, title="t", content="c")

    assert len(out) == _RELATION_LIMIT
    # Order is preserved: the first _RELATION_LIMIT hits, in search order.
    assert [m["id"] for m in out] == [h.id for h in hits[:_RELATION_LIMIT]]
