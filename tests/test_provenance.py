"""Provenance: how the writer came to believe a memory, declared at write time.

``confidence`` says whether a memory was saved accurately; it cannot say whether
the claim in it was measured, read from a file, told by the user, or concluded
by the agent. Those four arrive stamped identically without this field, so a
stored opinion reads as settled as a stored measurement.

It is a new column, not a vocabulary laid over ``source``. Measured on a real
brain, ``source`` holds 1061 distinct values across 1233 rows and mostly records
the *occasion* a memory was written ("session 2026-07-24", "/sink-the-ship"),
which is a different axis. The vocabulary is enforced: a controlled field that
accepts anything is free text again.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from pydantic import ValidationError

from gingugu.models import Memory, Provenance
from gingugu.portability import export_data, import_data

VOCABULARY = ["user-asserted", "measured", "file-derived", "self-concluded"]


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "prov.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "prov")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _call(server, tool: str, args: dict) -> dict:
    return _payload(await server.call_tool(tool, args))


async def _store(server, **extra) -> dict:
    args = {"title": "a claim", "content": "the claim itself", "type": "fact", **extra}
    return await _call(server, "memory_store", args)


# --- the vocabulary ---------------------------------------------------------


def test_the_vocabulary_is_exactly_the_approved_four():
    assert [p.value for p in Provenance] == VOCABULARY


def test_the_model_rejects_a_value_outside_the_vocabulary():
    with pytest.raises(ValidationError):
        Memory(
            id="x",
            namespace_id="n",
            type="fact",
            title="t",
            content="c",
            provenance="a hunch",
            created_at="now",
            updated_at="now",
            last_accessed="now",
        )


# --- store --------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("value", VOCABULARY)
async def test_store_records_each_declared_value(server, value):
    res = await _store(server, provenance=value)
    assert res["ok"], res
    assert res["memory"]["provenance"] == value


@pytest.mark.asyncio
async def test_store_rejects_an_undeclared_value_and_writes_nothing(server):
    res = await _store(server, provenance="gut feeling")
    assert res["ok"] is False
    for value in VOCABULARY:
        assert value in res["error"]
    found = await _call(server, "memory_search", {"query": "claim"})
    assert found["count"] == 0


@pytest.mark.asyncio
async def test_undeclared_provenance_is_absent_from_the_payload(server):
    """NULL means "never declared". A `null` key on every legacy memory is noise."""
    res = await _store(server)
    assert "provenance" not in res["memory"]


@pytest.mark.asyncio
async def test_provenance_leaves_source_alone(server):
    res = await _store(server, provenance="measured", source="session 2026-09-28")
    # Read the row directly: `source` is not part of any payload.
    with sqlite3.connect(os.environ["MEMORY_DB_PATH"]) as conn:
        row = conn.execute(
            "SELECT source, provenance FROM memories WHERE id = ?", (res["memory"]["id"],)
        ).fetchone()
    assert row == ("session 2026-09-28", "measured")


# --- read surfaces ------------------------------------------------------------


@pytest.mark.asyncio
async def test_compact_reads_carry_provenance(server):
    """The point is that a self-concluded claim arrives visibly contestable, and
    session start reads compact - so compact must carry it."""
    await _store(server, provenance="self-concluded")
    for tool, args in (
        ("memory_recall", {"query": "claim", "compact": True}),
        ("memory_search", {"query": "claim", "compact": True}),
        ("memory_context", {"task_hint": "claim", "compact": True}),
    ):
        res = await _call(server, tool, args)
        assert res["ok"], (tool, res)
        assert res["memories"][0]["provenance"] == "self-concluded", tool


# --- update -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_declares_corrects_and_clears(server):
    mem_id = (await _store(server))["memory"]["id"]

    set_ = await _call(server, "memory_update", {"memory_id": mem_id, "provenance": "measured"})
    assert set_["memory"]["provenance"] == "measured"

    fixed = await _call(
        server, "memory_update", {"memory_id": mem_id, "provenance": "self-concluded"}
    )
    assert fixed["memory"]["provenance"] == "self-concluded"

    untouched = await _call(server, "memory_update", {"memory_id": mem_id, "tags": "x"})
    assert untouched["memory"]["provenance"] == "self-concluded"

    cleared = await _call(server, "memory_update", {"memory_id": mem_id, "provenance": ""})
    assert "provenance" not in cleared["memory"]


@pytest.mark.asyncio
async def test_update_rejects_an_undeclared_value_and_changes_nothing(server):
    mem_id = (await _store(server, provenance="measured"))["memory"]["id"]
    res = await _call(server, "memory_update", {"memory_id": mem_id, "provenance": "vibes"})
    assert res["ok"] is False
    got = await _call(server, "memory_search", {"ids": mem_id})
    assert got["memories"][0]["provenance"] == "measured"


def test_declaring_provenance_does_not_confirm_the_claim(store, namespaces, db):
    """Saying how a claim was reached is not re-checking it, so the staleness
    clock stays where it was - same rule as a tag or metadata edit."""
    ns = namespaces.get_or_create("prov")
    mem = store.create(namespace_id=ns.id, type="fact", title="t", content="c")
    past = "2026-01-01T00:00:00+00:00"
    db.conn.execute("UPDATE memories SET last_confirmed = ? WHERE id = ?", (past, mem.id))
    db.conn.commit()
    after = store.update(mem.id, provenance=Provenance.MEASURED)
    assert after.provenance == Provenance.MEASURED
    assert after.last_confirmed == past


# --- export / import ----------------------------------------------------------


def test_export_import_round_trips_provenance(store, namespaces, db):
    ns = namespaces.get_or_create("prov")
    store.create(
        namespace_id=ns.id,
        type="fact",
        title="t",
        content="c",
        provenance=Provenance.FILE_DERIVED,
    )
    data = export_data(db.conn)
    db.conn.execute("DELETE FROM memories")
    import_data(db.conn, data)
    row = db.conn.execute("SELECT provenance FROM memories").fetchone()
    assert row[0] == "file-derived"


def test_import_rejects_an_undeclared_provenance_before_writing(store, namespaces, db):
    ns = namespaces.get_or_create("prov")
    store.create(namespace_id=ns.id, type="fact", title="t", content="c")
    data = export_data(db.conn)
    db.conn.execute("DELETE FROM memories")
    data["memories"][0]["provenance"] = "hearsay"
    with pytest.raises(ValueError, match="provenance"):
        import_data(db.conn, data)
    assert db.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_an_export_written_before_provenance_existed_still_imports(store, namespaces, db):
    ns = namespaces.get_or_create("prov")
    store.create(namespace_id=ns.id, type="fact", title="t", content="c")
    data = export_data(db.conn)
    db.conn.execute("DELETE FROM memories")
    for mem in data["memories"]:
        mem.pop("provenance", None)
        mem.pop("about", None)
    import_data(db.conn, data)
    row = db.conn.execute("SELECT provenance, about FROM memories").fetchone()
    assert tuple(row) == (None, None)
