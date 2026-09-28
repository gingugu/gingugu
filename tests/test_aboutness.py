"""Aboutness: what a memory is FOR, in the user's words, as distinct from what was done.

A memory is written in its author's vocabulary. Its owner names the same thing by
what it accomplishes, and nothing bridged the two: a workstream with four
memories took four queries to reach, because not one of them contained the word
the user used for it.

``about`` only closes that gap if retrieval can see it, so it is indexed on both
sides of the hybrid: an FTS5 column for the lexical side, and part of the text
the embedder reads for the semantic side. Rows with no ``about`` produce exactly
the text they always did, so nothing already embedded goes stale.
"""

from __future__ import annotations

import json

import pytest

from gingugu import chunking, embedding_sync
from gingugu.embedding_text import embedding_input
from gingugu.models import MemoryType
from gingugu.storage import MemoryStore
from tests.test_chunking import TruncatingEmbedder
from tests.test_embeddings import FakeEmbedder


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "about.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "about")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _call(server, tool: str, args: dict) -> dict:
    return _payload(await server.call_tool(tool, args))


_WRITTEN = {
    "title": "init writes managed hook blocks into settings.json",
    "content": "Added a merge step so reruns replace the managed block in place.",
    "type": "workflow",
}


# --- the embedding recipe -----------------------------------------------------


def test_the_recipe_is_unchanged_without_about():
    """Every vector already stored was built from this exact text."""
    assert embedding_input("T", "C") == "T\n\nC"
    assert embedding_input("T", "C", None) == "T\n\nC"
    assert embedding_input("T", "C", "") == "T\n\nC"


def test_about_sits_between_title_and_content():
    """Inside the head, so a long body cannot push it past the encoder's window."""
    assert embedding_input("T", "C", "A") == "T\n\nA\n\nC"


# --- the lexical side -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_word_only_in_about_is_found(server):
    """The witness, in miniature: the user's word is nowhere in the text."""
    stored = await _call(server, "memory_store", {**_WRITTEN, "about": "claude code bootstrapping"})
    assert stored["ok"], stored
    for tool in ("memory_recall", "memory_search"):
        res = await _call(server, tool, {"query": "bootstrapping"})
        assert [m["id"] for m in res["memories"]] == [stored["memory"]["id"]], tool


@pytest.mark.asyncio
async def test_without_about_the_same_query_finds_nothing(server):
    await _call(server, "memory_store", _WRITTEN)
    res = await _call(server, "memory_recall", {"query": "bootstrapping"})
    assert res["count"] == 0


@pytest.mark.asyncio
async def test_changing_about_reindexes(server):
    mem_id = (await _call(server, "memory_store", {**_WRITTEN, "about": "bootstrapping"}))[
        "memory"
    ]["id"]
    await _call(server, "memory_update", {"memory_id": mem_id, "about": "onboarding"})
    assert (await _call(server, "memory_search", {"query": "bootstrapping"}))["count"] == 0
    assert (await _call(server, "memory_search", {"query": "onboarding"}))["count"] == 1

    await _call(server, "memory_update", {"memory_id": mem_id, "about": ""})
    assert (await _call(server, "memory_search", {"query": "onboarding"}))["count"] == 0


@pytest.mark.asyncio
async def test_about_is_in_full_payloads_only_when_set(server):
    with_about = await _call(server, "memory_store", {**_WRITTEN, "about": "bootstrapping"})
    assert with_about["memory"]["about"] == "bootstrapping"
    without = await _call(server, "memory_store", {**_WRITTEN, "title": "another"})
    assert "about" not in without["memory"]


# --- the semantic side ----------------------------------------------------------


def _vector(conn, memory_id: str) -> list[float]:
    return embedding_sync.get_one(conn, FakeEmbedder(), memory_id)


def test_about_reaches_the_stored_vector(db, namespaces):
    store = MemoryStore(db.conn, embedder=FakeEmbedder())
    ns = namespaces.get_or_create("about")
    mem = store.create(
        namespace_id=ns.id, type=MemoryType.FACT, title="t", content="c", about="alpha"
    )
    assert _vector(db.conn, mem.id) == [1.0, 0.0, 0.0, 0.0]


def test_changing_about_reembeds_without_confirming_the_claim(db, namespaces):
    """`about` is a description of purpose, not a restatement of the claim: it
    moves the vector and the index, and leaves the staleness clock alone."""
    store = MemoryStore(db.conn, embedder=FakeEmbedder())
    ns = namespaces.get_or_create("about")
    mem = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="t", content="c")
    past = "2026-01-01T00:00:00+00:00"
    db.conn.execute("UPDATE memories SET last_confirmed = ? WHERE id = ?", (past, mem.id))
    db.conn.commit()

    updated = store.update(mem.id, about="beta beta")
    assert updated.about == "beta beta"
    assert updated.last_confirmed == past
    assert _vector(db.conn, mem.id) == [0.0, 2.0, 0.0, 0.0]


def test_backfill_embeds_with_about(db, namespaces):
    ns = namespaces.get_or_create("about")
    bare = MemoryStore(db.conn)  # no embedder: the row is stored unembedded
    mem = bare.create(
        namespace_id=ns.id, type=MemoryType.FACT, title="t", content="c", about="gamma"
    )
    assert embedding_sync.backfill(db.conn, FakeEmbedder()) == 1
    assert _vector(db.conn, mem.id) == [0.0, 0.0, 1.0, 0.0]


# --- pieces agree on the text they slice -----------------------------------------


_LONG = " ".join(f"w{i}" for i in range(30))


def _spans(conn, memory_id: str) -> list[tuple[int, int]]:
    rows = conn.execute(
        "SELECT start_char, end_char FROM memory_chunks WHERE memory_id = ? ORDER BY chunk",
        (memory_id,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def test_piece_spans_cover_the_text_with_about(db, namespaces):
    store = MemoryStore(db.conn, embedder=TruncatingEmbedder())
    ns = namespaces.get_or_create("about")
    mem = store.create(
        namespace_id=ns.id, type=MemoryType.FACT, title="t", content=_LONG, about="four more words"
    )
    assert _spans(db.conn, mem.id)[-1][1] == len(embedding_input("t", _LONG, "four more words"))


def test_piece_backfill_slices_the_text_with_about(db, namespaces):
    store = MemoryStore(db.conn, embedder=TruncatingEmbedder())
    ns = namespaces.get_or_create("about")
    mem = store.create(
        namespace_id=ns.id, type=MemoryType.FACT, title="t", content=_LONG, about="four more words"
    )
    db.conn.execute("DELETE FROM memory_chunks")
    db.conn.commit()
    assert chunking.backfill(db.conn, TruncatingEmbedder()) == 1
    assert _spans(db.conn, mem.id)[-1][1] == len(embedding_input("t", _LONG, "four more words"))


def test_query_time_piece_choice_reads_the_same_text(db, namespaces, monkeypatch):
    """Spans are offsets into the recipe's text, so the reader must rebuild that
    same text. Dropping `about` here would slice every piece off by its length."""
    store = MemoryStore(db.conn, embedder=TruncatingEmbedder())
    ns = namespaces.get_or_create("about")
    mem = store.create(
        namespace_id=ns.id, type=MemoryType.FACT, title="t", content=_LONG, about="four more words"
    )
    seen: list[list[str]] = []
    monkeypatch.setattr(chunking, "choose_piece", lambda terms, pieces: seen.append(pieces) or 0)
    chunking.cohort_vectors(db.conn, TruncatingEmbedder(), "w29", [mem.id])
    text = embedding_input("t", _LONG, "four more words")
    assert seen and seen[0][0] == text[: len(seen[0][0])]
    assert seen[0][-1] == text[_spans(db.conn, mem.id)[-1][0] :]
