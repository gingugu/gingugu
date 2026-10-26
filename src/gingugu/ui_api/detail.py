"""``/memory/<id>``: one memory in full, with everything hanging off it."""

from __future__ import annotations

import sqlite3

from ..models import MEMORY_COLUMNS, memory_columns_sql
from .env import ApiError

MAX_ACCESSES = 200


def _linked(conn: sqlite3.Connection, here: str, there: str, memory_id: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT m.id, r.relation_type, m.title FROM relations r"
        f" JOIN memories m ON m.id = r.{there} WHERE r.{here} = ?"
        " ORDER BY r.created_at, r.id",
        (memory_id,),
    )
    return [{"id": r["id"], "type": r["relation_type"], "title": r["title"]} for r in rows]


def memory_detail(conn: sqlite3.Connection, memory_id: str) -> dict:
    row = conn.execute(
        f"SELECT {memory_columns_sql('m.')}, n.name AS namespace FROM memories m"
        " JOIN namespaces n ON n.id = m.namespace_id WHERE m.id = ?",
        (memory_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, f"no memory {memory_id}")
    memory = {col: row[col] for col in MEMORY_COLUMNS}
    memory["pinned"] = bool(memory["pinned"])
    memory["namespace"] = row["namespace"]
    memory["tags"] = [
        r["name"]
        for r in conn.execute(
            "SELECT t.name FROM memory_tags mt JOIN tags t ON t.id = mt.tag_id"
            " WHERE mt.memory_id = ? ORDER BY t.name",
            (memory_id,),
        )
    ]
    claims = [
        dict(r)
        for r in conn.execute(
            "SELECT kind, ref, state, resolved_state, resolved_at FROM memory_claims"
            " WHERE memory_id = ? ORDER BY created_at, id",
            (memory_id,),
        )
    ]
    wires = [
        dict(r)
        for r in conn.execute(
            "SELECT id, tool_pattern, input_pattern FROM tripwires"
            " WHERE memory_id = ? ORDER BY created_at, id",
            (memory_id,),
        )
    ]
    accesses = [
        r["accessed_at"]
        for r in conn.execute(
            "SELECT accessed_at FROM access_log WHERE memory_id = ?"
            " ORDER BY accessed_at DESC LIMIT ?",
            (memory_id, MAX_ACCESSES),
        )
    ]
    return {
        "ok": True,
        "memory": memory,
        "relations": {
            "out": _linked(conn, "source_id", "target_id", memory_id),
            "in": _linked(conn, "target_id", "source_id", memory_id),
        },
        "claims": claims,
        "tripwires": wires,
        "accesses": accesses,
    }
