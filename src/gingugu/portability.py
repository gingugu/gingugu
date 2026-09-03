"""Export / import of memory data as portable JSON (backup & transfer).

Covers namespaces, memories, tags, and relations. Credentials are intentionally
excluded — their secrets live in the OS keychain, not the database, so a partial
export would be misleading and a security smell.

Export is keyed by namespace *name* on import: a payload moved to a fresh machine
re-binds memories to the local namespace of the same name (creating it if absent),
so the original namespace UUIDs never need to survive the trip.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from . import embedding_sync, tags
from .embeddings import EmbeddingProvider
from .models import (
    MEMORY_COLUMNS,
    Confidence,
    MemoryType,
    RelationType,
    memory_columns_sql,
    memory_placeholders_sql,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

EXPORT_FORMAT_VERSION = 1

_MEMORY_COLUMNS = memory_columns_sql()
_NAMESPACE_COLUMNS = "id, name, path, description, created_at, updated_at"
_RELATION_COLUMNS = "id, source_id, target_id, relation_type, created_at, metadata"


def export_data(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
    include_deprecated: bool = True,
) -> dict:
    """Serialize memories (+ tags), relations, and namespaces to a JSON-safe dict."""
    mem_where = []
    mem_params: list = []
    if namespace_id is not None:
        mem_where.append("namespace_id = ?")
        mem_params.append(namespace_id)
    if not include_deprecated:
        mem_where.append("confidence != 'deprecated'")
    where_sql = (" WHERE " + " AND ".join(mem_where)) if mem_where else ""

    mem_rows = conn.execute(
        f"SELECT {_MEMORY_COLUMNS} FROM memories{where_sql} ORDER BY created_at", mem_params
    ).fetchall()
    memories = []
    mem_ids: set[str] = set()
    for row in mem_rows:
        mem = dict(row)
        mem["tags"] = [
            r["name"]
            for r in conn.execute(
                "SELECT t.name FROM tags t JOIN memory_tags mt ON mt.tag_id = t.id "
                "WHERE mt.memory_id = ? ORDER BY t.name",
                (mem["id"],),
            ).fetchall()
        ]
        memories.append(mem)
        mem_ids.add(mem["id"])

    if namespace_id is not None:
        ns_rows = conn.execute(
            f"SELECT {_NAMESPACE_COLUMNS} FROM namespaces WHERE id = ?", (namespace_id,)
        ).fetchall()
    else:
        ns_rows = conn.execute(f"SELECT {_NAMESPACE_COLUMNS} FROM namespaces").fetchall()
    namespaces = [dict(r) for r in ns_rows]

    relations = [
        dict(r)
        for r in conn.execute(f"SELECT {_RELATION_COLUMNS} FROM relations").fetchall()
        # Only edges fully contained in the exported memory set survive.
        if r["source_id"] in mem_ids and r["target_id"] in mem_ids
    ]

    return {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": utcnow_iso(),
        "namespaces": namespaces,
        "memories": memories,
        "relations": relations,
    }


def _resolve_namespaces(conn: sqlite3.Connection, namespaces: list[dict]) -> tuple[dict, int]:
    """Map exported namespace ids to local ids (by name), creating missing ones.

    Returns ``(id_map, created_count)``.
    """
    id_map: dict[str, str] = {}
    created = 0
    for ns in namespaces:
        row = conn.execute("SELECT id FROM namespaces WHERE name = ?", (ns["name"],)).fetchone()
        if row is not None:
            id_map[ns["id"]] = row["id"]
            continue
        local_id = str(uuid.uuid4())
        now = utcnow_iso()
        conn.execute(
            "INSERT INTO namespaces(id, name, path, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                local_id,
                ns["name"],
                ns.get("path"),
                ns.get("description"),
                ns.get("created_at") or now,
                ns.get("updated_at") or now,
            ),
        )
        id_map[ns["id"]] = local_id
        created += 1
    return id_map, created


def _validate_payload(data: dict) -> None:
    """Reject invalid enum values *before* any insert.

    A row with a bad ``type``/``confidence`` would otherwise land in the DB and
    break every later recall/search when model validation hits it.
    """
    valid_types = {t.value for t in MemoryType}
    valid_confidences = {c.value for c in Confidence}
    valid_relations = {r.value for r in RelationType}
    for mem in data["memories"]:
        if mem.get("type") not in valid_types:
            raise ValueError(f"memory {mem.get('id')!r} has invalid type {mem.get('type')!r}")
        if mem.get("confidence") not in valid_confidences:
            raise ValueError(
                f"memory {mem.get('id')!r} has invalid confidence {mem.get('confidence')!r}"
            )
    for rel in data.get("relations", []):
        if rel.get("relation_type") not in valid_relations:
            raise ValueError(
                f"relation {rel.get('id')!r} has invalid relation_type "
                f"{rel.get('relation_type')!r}"
            )


def import_data(
    conn: sqlite3.Connection,
    data: dict,
    *,
    on_conflict: str = "skip",
    embedder: EmbeddingProvider | None = None,
) -> dict:
    """Restore a payload from :func:`export_data`. Returns a summary of changes.

    ``on_conflict`` is ``skip`` (leave existing memories) or ``replace``
    (overwrite memories sharing an id). Raises ``ValueError`` on a malformed
    payload.

    ``embedder`` encodes the imported memories so they are semantically
    searchable. Without it they are reachable by keyword only: the FTS5 index
    has triggers and keeps itself in step, but ``memory_embeddings`` has none,
    so a vector exists only where some code path deliberately wrote one. The
    summary reports ``embeddings_written``.

    Vectors are recomputed rather than carried in the payload. They are
    model-specific - a 384-dim BGE export restored on a host running a 768-dim
    model would have to be discarded on arrival - so shipping them would add
    ~1.5KB per memory to an export that is meant to stay portable and legible,
    for data that is derived from the text sitting beside it.
    """
    if not isinstance(data, dict) or "memories" not in data or "namespaces" not in data:
        raise ValueError("malformed export payload: missing 'memories'/'namespaces'")
    if on_conflict not in ("skip", "replace"):
        raise ValueError(f"invalid on_conflict {on_conflict!r}")
    _validate_payload(data)

    ns_map, ns_created = _resolve_namespaces(conn, data.get("namespaces", []))

    imported = skipped = replaced = 0
    written_ids: list[str] = []
    for mem in data["memories"]:
        local_ns = ns_map.get(mem["namespace_id"])
        if local_ns is None:
            # Memory references a namespace not present in the payload — skip it.
            skipped += 1
            continue
        exists = conn.execute("SELECT 1 FROM memories WHERE id = ?", (mem["id"],)).fetchone()
        if exists is not None:
            if on_conflict == "skip":
                skipped += 1
                continue
            conn.execute("DELETE FROM memories WHERE id = ?", (mem["id"],))
            replaced += 1
        else:
            imported += 1
        conn.execute(
            f"INSERT INTO memories({_MEMORY_COLUMNS}) " f"VALUES ({memory_placeholders_sql()})",
            {
                **{k: mem.get(k) for k in MEMORY_COLUMNS},
                # `pinned` is NOT NULL, and an export written before it existed
                # has no such key - `mem.get` would hand SQLite a None and fail
                # the insert. Absent means unpinned, which is also the honest
                # reading: that file never recorded a pin either way.
                "pinned": int(bool(mem.get("pinned"))),
                "namespace_id": local_ns,
            },
        )
        written_ids.append(mem["id"])
        for tag_name in mem.get("tags", []):
            tag_id = tags.get_or_create(conn, tag_name)
            conn.execute(
                "INSERT OR IGNORE INTO memory_tags(memory_id, tag_id) VALUES (?, ?)",
                (mem["id"], tag_id),
            )

    relations_imported = 0
    for rel in data.get("relations", []):
        present = conn.execute(
            "SELECT (SELECT 1 FROM memories WHERE id = ?) "
            "AND (SELECT 1 FROM memories WHERE id = ?)",
            (rel["source_id"], rel["target_id"]),
        ).fetchone()[0]
        if not present:
            continue
        cur = conn.execute(
            f"INSERT OR IGNORE INTO relations({_RELATION_COLUMNS}) "
            "VALUES (:id, :source_id, :target_id, :relation_type, :created_at, :metadata)",
            {
                "id": rel.get("id") or str(uuid.uuid4()),
                "source_id": rel["source_id"],
                "target_id": rel["target_id"],
                "relation_type": rel["relation_type"],
                "created_at": rel.get("created_at") or utcnow_iso(),
                "metadata": rel.get("metadata"),
            },
        )
        relations_imported += cur.rowcount

    if replaced:
        # Replacing memories can orphan tag rows the new tag sets no longer use.
        conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM memory_tags)")

    conn.commit()

    # Embed AFTER the commit: the rows must exist for `embed_ids` to read their
    # title/content back, and a failure here must not cost us the import. An
    # encoding failure is already swallowed and logged, and leaves the memories
    # keyword-searchable and eligible for the startup backfill.
    embeddings_written = embedding_sync.embed_ids(conn, embedder, written_ids)

    summary = {
        "namespaces_created": ns_created,
        "memories_imported": imported,
        "memories_replaced": replaced,
        "memories_skipped": skipped,
        "relations_imported": relations_imported,
        "embeddings_written": embeddings_written,
    }
    logger.info("Import complete: %s", summary)
    return summary
