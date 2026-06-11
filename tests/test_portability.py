"""Tests for export/import (portability.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gingugu import portability
from gingugu.config import Config
from gingugu.database import Database
from gingugu.models import Confidence, MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore


def _fresh_db() -> Database:
    cfg = Config(
        db_path=Path(":memory:"),
        namespace="test-ns",
        namespace_path=None,
        auto_context_limit=10,
        decay_lambda=0.05,
    )
    db = Database(cfg.db_path)
    db.connect()
    return db


def test_export_shape(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns = namespaces.get_or_create("proj")
    store.create(namespace_id=ns.id, type=MemoryType.FACT, title="a", content="x", tags=["t1"])
    payload = portability.export_data(store.conn)
    assert payload["format_version"] == portability.EXPORT_FORMAT_VERSION
    assert len(payload["memories"]) == 1
    assert payload["memories"][0]["tags"] == ["t1"]
    assert any(n["name"] == "proj" for n in payload["namespaces"])


def test_export_namespace_scoped(store: MemoryStore, namespaces: NamespaceManager) -> None:
    a = namespaces.get_or_create("a")
    b = namespaces.get_or_create("b")
    store.create(namespace_id=a.id, type=MemoryType.FACT, title="ma", content="x")
    store.create(namespace_id=b.id, type=MemoryType.FACT, title="mb", content="y")
    payload = portability.export_data(store.conn, namespace_id=a.id)
    assert len(payload["memories"]) == 1
    assert payload["memories"][0]["title"] == "ma"


def test_export_excludes_deprecated_when_requested(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    ns = namespaces.get_or_create("proj")
    store.create(namespace_id=ns.id, type=MemoryType.FACT, title="live", content="x")
    store.create(
        namespace_id=ns.id,
        type=MemoryType.FACT,
        title="dead",
        content="y",
        confidence=Confidence.DEPRECATED,
    )
    payload = portability.export_data(store.conn, include_deprecated=False)
    assert {m["title"] for m in payload["memories"]} == {"live"}


def test_roundtrip_to_fresh_db(
    store: MemoryStore, namespaces: NamespaceManager, relations: RelationManager
) -> None:
    ns = namespaces.get_or_create("proj")
    a = store.create(
        namespace_id=ns.id, type=MemoryType.FACT, title="a", content="x", tags=["shared"]
    )
    b = store.create(
        namespace_id=ns.id, type=MemoryType.PATTERN, title="b", content="y", tags=["shared"]
    )
    relations.relate(source_id=a.id, target_id=b.id, relation_type=RelationType.RELATED_TO)
    payload = portability.export_data(store.conn)

    dest = _fresh_db()
    try:
        result = portability.import_data(dest.conn, payload)
        assert result["memories_imported"] == 2
        assert result["relations_imported"] == 1
        assert result["namespaces_created"] == 1
        dest_store = MemoryStore(dest.conn)
        imported = dest_store.get(a.id, record_access=False)
        assert imported.title == "a"
        assert imported.tags == ["shared"]
        dest_rel = RelationManager(dest.conn)
        assert dest_rel.related_ids(a.id) == [b.id]
    finally:
        dest.close()


def test_import_skip_on_conflict(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns = namespaces.get_or_create("proj")
    m = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="orig", content="x")
    payload = portability.export_data(store.conn)
    payload["memories"][0]["title"] = "changed"

    result = portability.import_data(store.conn, payload, on_conflict="skip")
    assert result["memories_skipped"] == 1
    assert store.get(m.id, record_access=False).title == "orig"


def test_import_replace_on_conflict(store: MemoryStore, namespaces: NamespaceManager) -> None:
    ns = namespaces.get_or_create("proj")
    m = store.create(namespace_id=ns.id, type=MemoryType.FACT, title="orig", content="x")
    payload = portability.export_data(store.conn)
    payload["memories"][0]["title"] = "changed"

    result = portability.import_data(store.conn, payload, on_conflict="replace")
    assert result["memories_replaced"] == 1
    assert store.get(m.id, record_access=False).title == "changed"


def test_import_replace_prunes_orphan_tags(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # Regression: replacing a memory whose new tag set drops a tag used to
    # leave the orphaned row behind in `tags`.
    ns = namespaces.get_or_create("proj")
    m = store.create(
        namespace_id=ns.id, type=MemoryType.FACT, title="orig", content="x", tags=["old-tag"]
    )
    payload = portability.export_data(store.conn)
    payload["memories"][0]["tags"] = ["new-tag"]

    portability.import_data(store.conn, payload, on_conflict="replace")
    assert store.get(m.id, record_access=False).tags == ["new-tag"]
    names = {r["name"] for r in store.conn.execute("SELECT name FROM tags").fetchall()}
    assert names == {"new-tag"}


def test_import_malformed_rejected(store: MemoryStore) -> None:
    with pytest.raises(ValueError):
        portability.import_data(store.conn, {"nope": True})


def test_import_invalid_conflict_rejected(store: MemoryStore) -> None:
    payload = {"namespaces": [], "memories": []}
    with pytest.raises(ValueError):
        portability.import_data(store.conn, payload, on_conflict="bogus")


def test_import_rejects_invalid_enums_before_insert(
    store: MemoryStore, namespaces: NamespaceManager
) -> None:
    # Regression: a payload with a bad type/confidence used to land in the DB
    # and break every later recall/search at model-validation time.
    ns = namespaces.get_or_create("proj")
    store.create(namespace_id=ns.id, type=MemoryType.FACT, title="good", content="x")
    payload = portability.export_data(store.conn)

    bad_type = {**payload, "memories": [{**payload["memories"][0], "id": "new", "type": "bogus"}]}
    with pytest.raises(ValueError, match="invalid type"):
        portability.import_data(store.conn, bad_type)

    bad_conf = {
        **payload,
        "memories": [{**payload["memories"][0], "id": "new2", "confidence": "certain"}],
    }
    with pytest.raises(ValueError, match="invalid confidence"):
        portability.import_data(store.conn, bad_conf)

    bad_rel = {
        **payload,
        "relations": [
            {
                "id": "r1",
                "source_id": payload["memories"][0]["id"],
                "target_id": payload["memories"][0]["id"],
                "relation_type": "knows_about",
            }
        ],
    }
    with pytest.raises(ValueError, match="invalid relation_type"):
        portability.import_data(store.conn, bad_rel)

    # Nothing invalid was inserted.
    total = store.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert total == 1
