"""Unit tests for the ``memory_context`` buckets themselves.

``build_context`` is covered for membership and position, but every one of those
assertions reaches the buckets through the quota machinery, which can fill a
slot from the backfill pool and mask a wrong ORDER BY underneath. These tests
call the bucket functions directly, so the ordering each bucket promises is
asserted at the only layer where it is actually decided.
"""

from __future__ import annotations

from gingugu.context_buckets import recently_active
from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


def test_recently_active_orders_by_write_not_read(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # The sort key guard. This bucket exists so a freshly-stored, never-read
    # memory survives the cut, so it must order by a WRITE timestamp. It used to
    # order by `last_accessed` - a read timestamp - which answered the opposite
    # question and let anything merely looked at outrank anything newly written.
    ns_id = namespaces.get_or_create("test-ns").id
    older = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="older", content="written first"
    )
    newer = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="newer", content="written second"
    )
    # Read the older one repeatedly. Reads move `last_accessed` and nothing else.
    for _ in range(20):
        store.record_accesses([older.id])
    rows = recently_active(store.conn, ns_id, limit=10)
    assert [m.id for m in rows] == [newer.id, older.id]


def test_recently_active_breaks_a_timestamp_tie_toward_the_later_write(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # A timestamp is only as fine as the clock that made it: Windows resolved
    # `datetime.now()` to 15.6ms before Python 3.13, so two stores in one tick
    # share an `updated_at` byte for byte. The tie is forced here rather than
    # raced for, because on a microsecond clock it never occurs at all - and an
    # unspecified tie sorts rowid ASCENDING, returning the older memory first.
    ns_id = namespaces.get_or_create("test-ns").id
    first = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="first", content="same tick"
    )
    second = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="second", content="same tick"
    )
    store.conn.execute("UPDATE memories SET updated_at = '2026-01-01T00:00:00+00:00'")
    store.conn.commit()
    rows = recently_active(store.conn, ns_id, limit=10)
    assert [m.id for m in rows] == [second.id, first.id]


def test_recently_active_lifts_an_edited_memory(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # The flip side: an edit IS a write, so it must re-surface the memory.
    # Without this the bucket would only ever track creation order.
    ns_id = namespaces.get_or_create("test-ns").id
    revised = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="revised", content="first draft"
    )
    store.create(namespace_id=ns_id, type=MemoryType.FACT, title="untouched", content="left alone")
    store.update(revised.id, content="rewritten with what we actually learned")
    rows = recently_active(store.conn, ns_id, limit=10)
    assert rows[0].id == revised.id


def test_recently_active_excludes_pinned_and_deprecated(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # Pins are served by their own tier and deprecated memories are not served
    # at all. Both are filtered in SQL, before LIMIT, so that a full pin tier
    # cannot starve this bucket by consuming its row budget.
    ns_id = namespaces.get_or_create("test-ns").id
    keeper = store.create(
        namespace_id=ns_id, type=MemoryType.FACT, title="keeper", content="ordinary"
    )
    pin = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="pin", content="always")
    dead = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="dead", content="gone")
    store.update(pin.id, pinned=True)
    store.update(dead.id, confidence=Confidence.DEPRECATED)
    rows = recently_active(store.conn, ns_id, limit=10)
    assert [m.id for m in rows] == [keeper.id]


def test_recently_active_scopes_to_its_namespace(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns_id = namespaces.get_or_create("mine").id
    other_ns = namespaces.get_or_create("theirs").id
    mine = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="mine", content="here")
    store.create(namespace_id=other_ns, type=MemoryType.FACT, title="theirs", content="elsewhere")
    rows = recently_active(store.conn, ns_id, limit=10)
    assert [m.id for m in rows] == [mine.id]
