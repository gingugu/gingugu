"""Capability pointers: a memory that says a thing EXISTS, not how to do it.

Memory recorded procedures and never artifacts. One integration's REST
mechanics were written down in full four separate times, and not one memory
said a script had been built - so it was rebuilt from the notes, again. A
procedure recorded four times is four invitations to rebuild it; a pointer to
the artifact, recorded once, ends the question.

A ``capability`` memory carries its structure under a reserved metadata key:
``{"capability": {"run": "...", "path": "..."}}``. ``run`` is how to invoke it
and is required; ``path`` is where it lives and is optional, because some
capabilities are a command on PATH or an MCP tool and have no file to point at.

Two read-side behaviours, both plain arithmetic in keeping with the store's
rule that what surfaces is calculated, never judged:

* ``describe`` stats ``path`` on every read, so a script that was deleted shows
  up as a dead pointer instead of a confident instruction.
* ``lane`` is a second, capability-only pass for ``memory_recall``. Without it
  a pointer competes in the main ranking against the very procedure memories it
  exists to replace, and those are many, long, and frequently read.
"""

from __future__ import annotations

import json
import os
import sqlite3

from . import embedding_sync, grants
from .embeddings import EmbeddingProvider, cosine

CAPABILITY_TYPE = "capability"
CAPABILITY_KEY = "capability"
_FIELDS = frozenset({"run", "path"})

# Cosine floor for the lane, MEASURED against recall queries - not borrowed
# from the prompt gate. The gate's 0.78 was calibrated on whole prompts; recall
# queries are short and score lower against the same memory. Three sample
# capabilities (title + about + content, as embedded) against ten written
# queries and every query in a real `query_log` (~50):
#
#     relevant queries         0.647 - 0.852, most 0.69 - 0.75
#     unrelated logged queries max 0.667 (task-notification noise), p90 ~0.59
#
# The three highest "unrelated" hits (0.694, 0.760, 0.787) turned out to be
# real logged queries asking how to read a Jira ticket - the exact rebuild this
# feature exists to prevent. 0.78 would have missed most true hits; 0.68 sits
# just above the measured noise. Small sample: re-measure once real capability
# memories exist. A false hit costs two compact entries in a labelled section.
LANE_BAR = 0.68
LANE_LIMIT = 2

_LANE_SQL = """
SELECT id FROM memories
 WHERE type = 'capability'
   AND confidence != 'deprecated'
   AND id NOT IN (SELECT target_id FROM relations WHERE relation_type = 'supersedes')
"""


def read(metadata: str | None) -> dict | None:
    """The capability block from a stored metadata blob, or None."""
    if not metadata:
        return None
    try:
        parsed = json.loads(metadata)
    except (TypeError, ValueError):
        return None
    block = parsed.get(CAPABILITY_KEY) if isinstance(parsed, dict) else None
    return block if isinstance(block, dict) else None


def check(type_value: str, metadata: str | None) -> str | None:
    """Validate the final state of a write. Returns an error message, or None.

    The rule is an equivalence: a memory is a ``capability`` exactly when its
    metadata carries a valid capability block. Either half alone is a pointer
    that cannot be followed, or a block no read surface will ever show.
    """
    raw = None
    if metadata:
        try:
            parsed = json.loads(metadata)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            raw = parsed.get(CAPABILITY_KEY)

    if type_value != CAPABILITY_TYPE:
        if raw is not None:
            return f"metadata.{CAPABILITY_KEY} is reserved for type 'capability'"
        return None

    if not isinstance(raw, dict):
        return (
            "type 'capability' needs metadata.capability = "
            '{"run": "<how to invoke it>", "path": "<where it lives, optional>"}'
        )
    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        return f"metadata.capability has unknown field(s) {unknown}; allowed: run, path"
    run = raw.get("run")
    if not isinstance(run, str) or not run.strip():
        return "metadata.capability.run is required and must be a non-empty string"
    path = raw.get("path")
    if path is not None and (not isinstance(path, str) or not path.strip()):
        return "metadata.capability.path must be a non-empty string when given"
    return None


def resolve_path(path: str, base: str | None) -> str | None:
    """Absolute form of ``path``, or None when it cannot be located.

    A relative path means relative to the namespace's repo. With no repo path
    on record there is nothing to resolve it against, and guessing the server's
    working directory would make ``exists`` a statement about the wrong place.
    """
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return expanded
    if base:
        return os.path.join(os.path.expanduser(base), expanded)
    return None


def describe(block: dict, *, base: str | None, local: bool) -> dict:
    """The read-side shape: ``run``, plus ``path`` and ``exists`` when a path is set.

    ``exists`` is True or False only when it was actually checked. It is None
    when the path cannot be resolved, and None on a non-local transport: under
    ``gingugu serve`` the server's disk is not the caller's, so a False there
    would report the absence of a file the caller may well have.
    """
    out: dict = {"run": block.get("run")}
    path = block.get("path")
    if path:
        out["path"] = path
        resolved = resolve_path(path, base) if local else None
        out["exists"] = os.path.exists(resolved) if resolved else None
    return out


def lane(
    conn: sqlite3.Connection,
    embedder: EmbeddingProvider | None,
    query: str,
    *,
    namespace_id: str | list[str] | None,
    exclude: set[str],
    limit: int = LANE_LIMIT,
    bar: float = LANE_BAR,
) -> list[tuple[str, float]]:
    """Capability memories that clear ``bar`` for ``query``, best first.

    ``(memory_id, cosine)`` pairs, at most ``limit``, none in ``exclude``.
    Empty without a working embedder: no lexical fallback, because a Jaccard
    score has no calibrated floor for a short query and the lane must stay
    silent rather than guess.
    """
    if embedder is None or not getattr(embedder, "enabled", False) or not query.strip():
        return []
    try:
        query_vec = embedder.encode(query)
    except Exception:  # pragma: no cover - defensive
        return []
    if query_vec is None:
        return []

    sql, params = _LANE_SQL, []
    if isinstance(namespace_id, str):
        sql += " AND namespace_id = ?"
        params.append(namespace_id)
    elif namespace_id is not None:
        # An empty list is a scope that resolved to nothing - not "everywhere".
        if not namespace_id:
            return []
        sql += f" AND namespace_id IN ({', '.join('?' for _ in namespace_id)})"
        params.extend(namespace_id)
    fence, fence_params = grants.scope_clause("namespace_id")
    if fence is not None:
        sql += f" AND {fence}"
        params.extend(fence_params)
    ids = [r["id"] for r in conn.execute(sql, params).fetchall() if r["id"] not in exclude]
    if not ids:
        return []

    stored = embedding_sync.get_many(conn, embedder, ids)
    scored = [(mid, cosine(query_vec, vec)) for mid, vec in stored.items()]
    picked = [(mid, sim) for mid, sim in scored if sim >= bar]
    picked.sort(key=lambda x: (-x[1], x[0]))
    return picked[:limit]
