"""The `memories` column list is declared once, and these tests hold it there.

Four modules used to carry private copies of the column list. When `pinned` was
added only two of them gained it, and nothing failed: the readers built
`Memory(**row)` from a short list, the field took its default, and every search
path reported a confident `pinned=False` for genuinely pinned memories while
`memory_export` dropped the flag entirely. A drifted copy is silent by nature -
the SQL still parses and the model still constructs - so it needs a test that
compares the list against something outside itself.
"""

from __future__ import annotations

from pathlib import Path

from gingugu import context_buckets, portability, search_common, search_filters, storage
from gingugu.database import Database
from gingugu.models import (
    MEMORY_COLUMNS,
    NON_COLUMN_FIELDS,
    Memory,
    MemoryType,
    memory_columns_sql,
    memory_placeholders_sql,
)
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore


def _fresh_conn():
    db = Database(Path(":memory:"))
    return db.connect()


# --- the list is pinned to things outside itself ------------------------------


def test_columns_match_the_model():
    """Every persisted `Memory` field is a column, and vice versa."""
    persisted = set(Memory.model_fields) - NON_COLUMN_FIELDS
    assert set(MEMORY_COLUMNS) == persisted


def test_columns_match_the_live_schema():
    """The list matches what the migrations actually build.

    This is the half that catches a migration adding a column no reader knows
    about, which is the exact shape of the `pinned` drift.
    """
    conn = _fresh_conn()
    actual = [row[1] for row in conn.execute("SELECT * FROM pragma_table_info('memories')")]
    assert list(MEMORY_COLUMNS) == actual


def test_no_module_keeps_a_private_copy():
    """Every consumer derives its SQL from the one tuple."""
    expected = memory_columns_sql()
    assert storage._COLUMNS == expected
    # The context bucket SQL lives in context_buckets; the guard follows it.
    assert context_buckets._COLUMNS == expected
    assert portability._MEMORY_COLUMNS == expected
    assert search_common.BASE_COLUMNS == expected
    assert search_common.COLUMNS == memory_columns_sql("m.")


def test_placeholders_line_up_with_columns():
    """An INSERT's VALUES clause cannot drift out of step with its columns."""
    columns = memory_columns_sql().replace(" ", "").split(",")
    placeholders = memory_placeholders_sql().replace(" ", "").split(",")
    assert [f":{c}" for c in columns] == placeholders


# --- and the behaviour the drift actually broke --------------------------------


def test_pinned_is_reported_by_every_read_path(store: MemoryStore, namespaces: NamespaceManager):
    """The regression itself: a pinned row must read back as pinned.

    Asserted through the real query paths rather than by inspecting the column
    string, because the string being right is only half of it.
    """
    ns = namespaces.get_or_create("proj")
    mem = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="rule", content="always")
    store.conn.execute("UPDATE memories SET pinned = 1 WHERE id = ?", (mem.id,))
    store.conn.commit()

    found, _ = search_filters.fetch_by_ids(store.conn, [mem.id])
    assert found[0].pinned is True, "search path must report the stored pin"

    reread = store.get(mem.id)
    assert reread is not None and reread.pinned is True, "storage must report the stored pin"


def test_export_import_round_trip_preserves_pins(store: MemoryStore, namespaces: NamespaceManager):
    """Export then import must not silently unpin a memory.

    The data-integrity half. Export/import is the documented backup path, so a
    dropped `pinned` means restoring a backup quietly demotes exactly the
    memories that were marked never-lose-these.
    """
    ns = namespaces.get_or_create("proj")
    pinned = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="p", content="keep")
    store.create(namespace_id=ns.id, type=MemoryType.FACT, title="plain", content="ordinary")
    store.conn.execute("UPDATE memories SET pinned = 1 WHERE id = ?", (pinned.id,))
    store.conn.commit()

    payload = portability.export_data(store.conn, namespace_id=ns.id)
    exported = {m["id"]: m for m in payload["memories"]}
    assert exported[pinned.id]["pinned"], "export must carry the pin"

    dest = _fresh_conn()
    portability.import_data(dest, payload)

    row = dest.execute("SELECT pinned FROM memories WHERE id = ?", (pinned.id,)).fetchone()
    assert row is not None and row[0] == 1, "import must restore the pin"


def test_import_tolerates_an_export_written_before_pinned_existed(
    store: MemoryStore, namespaces: NamespaceManager
):
    """`pinned` is NOT NULL, so a missing key must become 0, not None.

    Export files written by an older gingugu have no such key. Handing SQLite a
    None there fails the insert and takes the whole restore down with it.
    """
    ns = namespaces.get_or_create("proj")
    store.create(namespace_id=ns.id, type=MemoryType.FACT, title="legacy", content="older")

    payload = portability.export_data(store.conn, namespace_id=ns.id)
    for mem in payload["memories"]:  # simulate a pre-`pinned` export
        mem.pop("pinned", None)

    dest = _fresh_conn()
    result = portability.import_data(dest, payload)

    assert result["memories_imported"] == 1
    assert dest.execute("SELECT pinned FROM memories").fetchone()[0] == 0
