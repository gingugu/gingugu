"""Unit tests for the ``suggest_relations`` hint helper.

The integration suite (``test_integration.py``) pins the structural contracts:
``suggested_relations`` is a list, disjoint from ``similar_memories``, skippable
via ``relation_check=False``, and excludes already-related memories. What it
does *not* assert is the positive path — that a genuine candidate actually gets
surfaced — because real hybrid-search scores aren't deterministic enough to
pin a single id.

These unit tests close that gap by mocking ``search_mod.search`` to control
which candidates retrieval hands over (stage 1), then letting the REAL absolute
similarity gate (stage 2) judge them. Candidates are made to pass or fail by
their text, not by a stamped score: the gate no longer reads the retrieval
score, so a test that stamped one would be testing nothing.

The suite runs with embeddings disabled (see ``offline_embeddings``), so the
gate takes its lexical branch and a candidate whose text is identical to the
payload scores 1.0 while one sharing no vocabulary scores 0.0.
"""

from __future__ import annotations

from gingugu.config import Config
from gingugu.database import Database
from gingugu.handlers import ServerContext, hints
from gingugu.handlers.hints import _RELATION_LIMIT, suggest_relations
from gingugu.models import MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore

# The payload every test below stores against, and two candidate bodies: one
# lexically identical to it (similarity 1.0) and one sharing no token with it
# (0.0). Both sit far from any plausible cutoff, so these tests assert the
# gate's BEHAVIOUR without pinning its calibration.
PAYLOAD_TITLE = "argocd sync drift on the staging cluster"
PAYLOAD_CONTENT = "the application controller reconciles a stale revision after a helm rollback"
FAR_TITLE = "sourdough starter feeding schedule"
FAR_CONTENT = "discard half, add flour and water, rest eight hours somewhere warm"


def _ctx(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config
) -> ServerContext:
    return ServerContext(config=config, store=store, namespaces=namespaces, conn=db.conn)


def _near(store: MemoryStore, ns_id: str, title: str):
    """A candidate whose body matches the payload: clears any similarity gate."""
    return store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title=title, content=PAYLOAD_CONTENT
    )


def _far(store: MemoryStore, ns_id: str, title: str):
    """A candidate sharing no vocabulary with the payload: fails every gate."""
    return store.create(namespace_id=ns_id, type=MemoryType.FACT, title=title, content=FAR_CONTENT)


def _patch_search(monkeypatch, hits: list) -> None:
    monkeypatch.setattr(hints.search_mod, "search", lambda *a, **k: list(hits))


def _suggest(ctx, ns_id, **kwargs):
    return suggest_relations(
        ctx,
        namespace_id=ns_id,
        title=PAYLOAD_TITLE,
        content=PAYLOAD_CONTENT,
        **kwargs,
    )


def test_candidate_above_threshold_is_suggested(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    hit = _near(store, ns_id, PAYLOAD_TITLE)
    _patch_search(monkeypatch, [hit])

    out = _suggest(_ctx(db, store, namespaces, config), ns_id, memory_id=None)

    assert [m["id"] for m in out] == [hit.id]
    assert out[0]["title"] == PAYLOAD_TITLE


def test_candidate_below_threshold_is_dropped(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    weak = _far(store, ns_id, FAR_TITLE)
    _patch_search(monkeypatch, [weak])

    out = _suggest(_ctx(db, store, namespaces, config), ns_id, memory_id=None)

    assert out == []


def test_excludes_self_and_explicit_exclude_ids(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    me = _near(store, ns_id, "self")
    dup = _near(store, ns_id, "already-surfaced-as-similar")
    keep = _near(store, ns_id, "real-candidate")
    # search returns all three, but self and the exclude-id must be filtered out.
    _patch_search(monkeypatch, [me, dup, keep])

    out = _suggest(
        _ctx(db, store, namespaces, config),
        ns_id,
        memory_id=me.id,
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
    source = _near(store, ns_id, "source")
    linked = _near(store, ns_id, "already-linked")
    fresh = _near(store, ns_id, "not-yet-linked")
    relations.relate(
        source_id=source.id, target_id=linked.id, relation_type=RelationType.RELATED_TO
    )
    # search surfaces both, but the existing edge to `linked` must exclude it.
    _patch_search(monkeypatch, [linked, fresh])

    out = _suggest(_ctx(db, store, namespaces, config), ns_id, memory_id=source.id)

    assert [m["id"] for m in out] == [fresh.id]


def test_respects_relation_limit(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    ns_id = namespaces.get_or_create("test-ns").id
    hits = [_near(store, ns_id, f"cand-{i}") for i in range(_RELATION_LIMIT + 2)]
    _patch_search(monkeypatch, hits)

    out = _suggest(_ctx(db, store, namespaces, config), ns_id, memory_id=None)

    assert len(out) == _RELATION_LIMIT


def test_mixed_pool_keeps_only_the_close_ones(
    db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config, monkeypatch
) -> None:
    """Retrieval always returns its best N; the gate is what makes them earn it."""
    ns_id = namespaces.get_or_create("test-ns").id
    close = _near(store, ns_id, "worth-examining")
    far_a = _far(store, ns_id, FAR_TITLE)
    far_b = _far(store, ns_id, "another unrelated note")
    _patch_search(monkeypatch, [far_a, close, far_b])

    out = _suggest(_ctx(db, store, namespaces, config), ns_id, memory_id=None)

    assert [m["id"] for m in out] == [close.id]
