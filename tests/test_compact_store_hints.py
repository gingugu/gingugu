"""Tests for compact write-time hints on ``memory_store`` / ``memory_update``.

``similar_memories`` and ``suggested_relations`` are unasked-for extras
attached to a WRITE. Returning full bodies charged the caller up to six
memories of context on every single store, however small the memory being
written. They are pointers now: title + a ~200-char excerpt, with
``memory_recall`` one call away when a candidate warrants a closer look.

The memory the caller just wrote is NOT compacted — that is the payload they
asked for.
"""

from __future__ import annotations

import json

import pytest


def _payload(result) -> dict:
    """Unwrap a FastMCP tool result into its JSON dict."""
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "hints.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "hints-ns")
    # Deterministic BM25-only retrieval: the hints must fire on title/term
    # overlap alone, with no cached fastembed model changing the ranking.
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


# Well past the ~200-char excerpt cap, so truncation is observable.
LONG = "argocd rollout detail " * 40


async def _store(server, *, title: str, content: str = LONG, **extra) -> dict:
    return _payload(
        await server.call_tool(
            "memory_store",
            {"content": content, "title": title, "type": "fact", **extra},
        )
    )


def _assert_compact_hint(hint: dict) -> None:
    assert "content" not in hint, "hints must never carry full bodies"
    assert "summary" in hint and len(hint["summary"]) <= 210
    assert hint["id"] and hint["title"]
    # Bookkeeping fields are dropped, exactly as on compact reads.
    assert "created_at" not in hint and "access_count" not in hint


@pytest.mark.asyncio
async def test_similar_memories_are_compact(server) -> None:
    first = await _store(server, title="argocd sync rollout")
    second = await _store(server, title="argocd sync rollout")
    assert first["ok"] and second["ok"]

    similar = second["similar_memories"]
    assert similar, "a near-identical title must still produce a dedupe hint"
    assert any(h["id"] == first["memory"]["id"] for h in similar)
    for hint in similar:
        _assert_compact_hint(hint)


def test_suggested_relations_are_compact(db, store, namespaces, config, monkeypatch) -> None:
    """Unit level with retrieval mocked - same reason as ``test_suggest_relations``:
    real hybrid scores aren't deterministic enough to pin a relation hit at
    the tool surface. ``memory_store`` and ``memory_update`` share this one
    helper, so covering it here covers both.

    The candidate clears the gate on its TEXT, not a stamped score: the hint no
    longer reads the retrieval score at all.
    """
    from gingugu.handlers import ServerContext, hints
    from gingugu.handlers.hints import suggest_relations
    from gingugu.models import MemoryType

    ns_id = namespaces.get_or_create("hints-unit").id
    hit = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="candidate", content=LONG)
    monkeypatch.setattr(hints.search_mod, "search", lambda *a, **k: [hit])

    ctx = ServerContext(config=config, store=store, namespaces=namespaces, conn=db.conn)
    out = suggest_relations(
        ctx, memory_id=None, namespace_id=ns_id, title="candidate", content=LONG
    )

    assert [m["id"] for m in out] == [hit.id]
    _assert_compact_hint(out[0])


@pytest.mark.asyncio
async def test_stored_memory_itself_keeps_full_content(server) -> None:
    """Regression guard: compacting the hints must not compact the write.

    The caller's own memory is the payload they asked for — round-tripping it
    is how a client confirms what landed.
    """
    await _store(server, title="argocd sync rollout")
    out = await _store(server, title="argocd sync rollout")

    assert out["memory"]["content"] == LONG
    assert "summary" not in out["memory"]
    # ...while the hint on the SAME response is compact.
    assert out["similar_memories"]
    assert "content" not in out["similar_memories"][0]


@pytest.mark.asyncio
async def test_hints_are_smaller_than_the_bodies_they_point_at(server) -> None:
    """The whole point of the change, asserted in bytes.

    Without this the hints would serialize larger than the memory being
    written — the exact inversion that motivated the fix.
    """
    await _store(server, title="argocd sync rollout")
    out = await _store(server, title="argocd sync rollout")

    hint_bytes = len(json.dumps(out["similar_memories"]))
    assert hint_bytes < len(LONG), "a hint must cost less than the body it points at"


@pytest.mark.asyncio
async def test_memory_update_hint_list_shape_survives(server) -> None:
    """``memory_update`` still returns a well-formed (possibly empty) hint list.

    The positive path is pinned at unit level above; this guards the tool
    surface against the swap breaking the response contract.
    """
    target = await _store(server, title="keycloak realm import")
    assert target["ok"]

    out = _payload(
        await server.call_tool(
            "memory_update",
            {
                "memory_id": target["memory"]["id"],
                "title": "keycloak realm export",
                "content": "keycloak realm detail " * 40,
            },
        )
    )
    assert out["ok"]
    for hint in out.get("suggested_relations") or []:
        _assert_compact_hint(hint)
