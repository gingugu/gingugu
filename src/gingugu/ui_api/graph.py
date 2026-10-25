"""``/info`` and ``/graph``: the light skeleton the Explorer draws.

Nodes carry metadata and tag indexes only - never content bodies - so a brain
of thousands of memories stays a modest payload. Deprecated memories are
included: the brain never forgets, and neither does its picture.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .. import __version__
from ..migrations import LATEST_SCHEMA_VERSION
from .tag_edges import tag_edges


def info(conn: sqlite3.Connection, params: dict[str, str]) -> dict:
    return {
        "ok": True,
        "version": __version__,
        "schema_version": LATEST_SCHEMA_VERSION,
        "memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "relations": conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
        "graph_version": graph_version(conn),
    }


def graph_version(conn: sqlite3.Connection) -> str:
    """Changes whenever the data does; cheap enough to poll."""
    count, newest = conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memories").fetchone()
    rels = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    return f"{count}:{newest or ''}:{rels}"


def _node(r: sqlite3.Row, tag_ids: list[int]) -> dict:
    return {
        "id": r["id"],
        "ns": r["namespace_id"],
        "type": r["type"],
        "conf": r["confidence"],
        "title": r["title"],
        "pinned": bool(r["pinned"]),
        "access": r["access_count"],
        "created": r["created_at"],
        "updated": r["updated_at"],
        "accessed": r["last_accessed"],
        "confirmed": r["last_confirmed"],
        "tags": tag_ids,
    }


def graph(conn: sqlite3.Connection, params: dict[str, str]) -> dict:
    namespaces = [
        {"id": r["id"], "name": r["name"], "count": r["n"]}
        for r in conn.execute(
            "SELECT n.id, n.name, COUNT(m.id) AS n FROM namespaces n"
            " LEFT JOIN memories m ON m.namespace_id = n.id GROUP BY n.id ORDER BY n.name"
        )
    ]
    tag_names: list[str] = []
    tag_index: dict[str, int] = {}
    tags_by_node: dict[str, list[str]] = defaultdict(list)
    for r in conn.execute(
        "SELECT mt.memory_id, t.name FROM memory_tags mt JOIN tags t ON t.id = mt.tag_id"
    ):
        tags_by_node[r["memory_id"]].append(r["name"])
        if r["name"] not in tag_index:
            tag_index[r["name"]] = len(tag_names)
            tag_names.append(r["name"])

    nodes = [
        _node(r, [tag_index[t] for t in tags_by_node.get(r["id"], [])])
        for r in conn.execute(
            "SELECT id, namespace_id, type, confidence, title, pinned, access_count,"
            " created_at, updated_at, last_accessed, last_confirmed FROM memories"
        )
    ]
    relations = [
        {
            "s": r["source_id"],
            "t": r["target_id"],
            "type": r["relation_type"],
            "created": r["created_at"],
        }
        for r in conn.execute(
            "SELECT source_id, target_id, relation_type, created_at FROM relations"
        )
    ]
    edges = tag_edges(
        {n["id"]: tags_by_node.get(n["id"], []) for n in nodes},
        include_dates=params.get("dates") == "1",
    )
    return {
        "ok": True,
        "version": graph_version(conn),
        "namespaces": namespaces,
        "tags": tag_names,
        "nodes": nodes,
        "relations": relations,
        "tag_edges": [{"s": a, "t": b, "w": w} for a, b, w in edges],
    }
