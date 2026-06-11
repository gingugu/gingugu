"""Adversarial / edge-case inputs — the stuff that only bites in production:
malformed queries, FTS5 special characters, unicode, huge content, junk tags."""

from __future__ import annotations

from gingugu import search as search_mod
from gingugu.models import MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.search import build_match_query
from gingugu.storage import MemoryStore


def test_punctuation_only_query_returns_empty(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("e").id
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="hello world")
    # No usable tokens -> no match query -> empty results, not an error.
    assert build_match_query("   !@#$%^&*()   ") is None
    assert search_mod.search(store.conn, query="!@#$ %^&*", namespace_id=ns_id) == []


def test_fts_special_chars_do_not_raise(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("e").id
    store.create(
        namespace_id=ns_id,
        type=MemoryType.PATTERN,
        title="pointers",
        content="raw pointers and memory management in cpp",
    )
    # FTS operator-ish junk must be neutralized by quoting (no syntax error).
    messy = 'pointers* AND (memory) OR "NEAR" -cpp'
    results = search_mod.search(store.conn, query=messy, namespace_id=ns_id)
    assert any(m.title == "pointers" for m in results)


def test_unicode_and_emoji_roundtrip(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("e").id
    store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="café résumé 日本語 ☕",
        content="naïve façade — emoji 🏴‍☠️ and accents café résumé",
    )
    results = search_mod.search(store.conn, query="café résumé", namespace_id=ns_id)
    assert len(results) == 1
    assert results[0].content.startswith("naïve")


def test_large_content_roundtrip(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("e").id
    big = "needle " + ("filler word " * 5000) + "haystack"
    mem = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="big", content=big)
    fetched = store.get(mem.id, record_access=False)
    assert fetched.content == big
    assert len(search_mod.search(store.conn, query="needle haystack", namespace_id=ns_id)) == 1


def test_junk_tags_are_normalized_or_dropped(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("e").id
    mem = store.create(
        namespace_id=ns_id,
        type=MemoryType.FACT,
        title="t",
        content="x",
        tags=["", "   ", "Multi  Word", "DUP", "dup"],
    )
    # Empty/whitespace dropped; normalized; de-duplicated (order is by tag name).
    assert set(mem.tags) == {"multi-word", "dup"}


def test_empty_query_string_returns_empty(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns_id = namespaces.get_or_create("e").id
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="t", content="hello")
    assert search_mod.search(store.conn, query="", namespace_id=ns_id) == []
    assert search_mod.search(store.conn, query="   ", namespace_id=ns_id) == []
