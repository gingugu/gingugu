"""``/search`` and ``/stats``: the Explorer's lens, as an observer.

``/search`` calls the core ``search()`` - the same hybrid ranking the agent
gets - but not the ``memory_recall`` handler, which credits accesses, logs the
query and spreads activation. Looking must not change what is being looked at.
"""

from __future__ import annotations

import sqlite3

from ..search import search
from ..stats import compute_stats
from .db import namespace_ids, parse_limit
from .env import ApiError, UiEnv

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def stats(conn: sqlite3.Connection, params: dict[str, str]) -> dict:
    ns = params.get("ns")
    ns_id = namespace_ids(conn, [ns])[0] if ns else None
    return {"ok": True, "stats": compute_stats(conn, namespace_id=ns_id, prune=False)}


def lens_search(conn: sqlite3.Connection, params: dict[str, str], env: UiEnv) -> dict:
    query = (params.get("q") or "").strip()
    if not query:
        raise ApiError(400, "q is required")
    limit = parse_limit(params, DEFAULT_LIMIT, MAX_LIMIT)
    names = [n.strip() for n in (params.get("ns") or "").split(",") if n.strip()]
    ids = namespace_ids(conn, names) if names else None
    found = search(
        conn,
        query=query,
        namespace_id=ids,
        limit=limit,
        weights=env.get_weights(),
        decay_lambda=env.get_decay_lambda(),
        embedder=env.get_embedder(),
    )
    ns_names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM namespaces")}
    hits = [
        {
            "id": m.id,
            "title": m.title,
            "namespace": ns_names.get(m.namespace_id, m.namespace_id),
            "type": m.type.value,
            "confidence": m.confidence.value,
            "score": round(m.score, 4) if m.score is not None else None,
            "score_breakdown": {k: round(v, 4) for k, v in (m.score_parts or {}).items()},
        }
        for m in found
    ]
    return {"ok": True, "count": len(hits), "hits": hits}
