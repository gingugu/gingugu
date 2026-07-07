"""Health metrics (``memory_stats``) and opportunistic access-log pruning."""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta

from .decay import DEPRECATE_SUGGEST_AFTER_DAYS, DORMANT_AFTER_DAYS
from .staleness import REVIEW_HINT_AFTER_DAYS, review_signals

logger = logging.getLogger(__name__)

ACCESS_LOG_RETENTION_DAYS = 90
_PRUNE_THROTTLE_SECONDS = 3600
_last_prune_at = 0.0


def _cutoff_iso(days: int, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return (now - timedelta(days=days)).isoformat()


def count_dormant(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Count active memories not accessed within ``DORMANT_AFTER_DAYS``.

    Dormancy is a **non-destructive signal** — resting, not rotting. Unlike the
    old ``flag_stale`` (removed), this never changes a memory's confidence. A
    dormant memory wakes back up the moment it is recalled, directly or via
    spreading activation through a related memory.
    """
    now = now or datetime.now(UTC)
    cutoff = _cutoff_iso(DORMANT_AFTER_DAYS, now)
    and_ns = " AND namespace_id = ?" if namespace_id else ""
    ns_params: tuple = (namespace_id,) if namespace_id else ()
    return _count(
        conn,
        "SELECT COUNT(*) FROM memories WHERE last_accessed < ? "
        "AND confidence != 'deprecated'" + and_ns,
        (cutoff, *ns_params),
    )


def prune_access_log(conn: sqlite3.Connection, *, force: bool = False) -> int:
    """Delete access_log rows older than the retention window.

    Throttled to at most once per hour per process (``force`` bypasses). Returns
    the number of rows deleted. Aggregate counts live on ``memories.access_count``
    so trimming the log is non-destructive to ranking.
    """
    global _last_prune_at
    now_mono = time.monotonic()
    if not force and (now_mono - _last_prune_at) < _PRUNE_THROTTLE_SECONDS:
        return 0
    _last_prune_at = now_mono
    cutoff = _cutoff_iso(ACCESS_LOG_RETENTION_DAYS)
    cur = conn.execute("DELETE FROM access_log WHERE accessed_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def compute_stats(conn: sqlite3.Connection, *, namespace_id: str | None = None) -> dict:
    """Health overview: counts, staleness, and per-type/confidence breakdowns."""
    prune_access_log(conn)

    ns_clause = " WHERE namespace_id = ?" if namespace_id else ""
    ns_params: tuple = (namespace_id,) if namespace_id else ()

    total = _count(conn, f"SELECT COUNT(*) FROM memories{ns_clause}", ns_params)

    by_type = {
        row["type"]: row["n"]
        for row in conn.execute(
            f"SELECT type, COUNT(*) AS n FROM memories{ns_clause} GROUP BY type", ns_params
        ).fetchall()
    }
    by_confidence = {
        row["confidence"]: row["n"]
        for row in conn.execute(
            f"SELECT confidence, COUNT(*) AS n FROM memories{ns_clause} GROUP BY confidence",
            ns_params,
        ).fetchall()
    }

    dormant_cutoff = _cutoff_iso(DORMANT_AFTER_DAYS)
    deprecate_cutoff = _cutoff_iso(DEPRECATE_SUGGEST_AFTER_DAYS)
    and_ns = " AND namespace_id = ?" if namespace_id else ""

    dormant_count = _count(
        conn,
        f"SELECT COUNT(*) FROM memories WHERE last_accessed < ? "
        f"AND confidence != 'deprecated'{and_ns}",
        (dormant_cutoff, *ns_params),
    )
    deprecate_suggest = _count(
        conn,
        f"SELECT COUNT(*) FROM memories WHERE last_confirmed IS NOT NULL "
        f"AND last_confirmed < ? AND confidence != 'deprecated'{and_ns}",
        (deprecate_cutoff, *ns_params),
    )

    namespaces = [
        {"name": row["name"], "count": row["n"]}
        for row in conn.execute(
            "SELECT n.name AS name, COUNT(m.id) AS n FROM namespaces n "
            "LEFT JOIN memories m ON m.namespace_id = n.id "
            "GROUP BY n.id ORDER BY n DESC"
        ).fetchall()
    ]

    return {
        "total_memories": total,
        "by_type": by_type,
        "by_confidence": by_confidence,
        "dormant_count": dormant_count,
        # Back-compat alias for older consumers; dormancy supersedes staleness.
        "stale_count": dormant_count,
        "deprecation_suggested": deprecate_suggest,
        "namespaces": namespaces,
        "access_log_rows": _count(conn, "SELECT COUNT(*) FROM access_log"),
        "credentials": _credential_health(conn),
        "hygiene": compute_hygiene(conn, namespace_id=namespace_id),
        "review": compute_review(conn, namespace_id=namespace_id),
    }


# Cap on how many review-flagged memories we list in the stats sample; the
# full count is always reported.
_REVIEW_SAMPLE_LIMIT = 5


def compute_review(conn: sqlite3.Connection, *, namespace_id: str | None = None) -> dict:
    """Advisory review sweep: point-in-time memories that may have gone stale.

    Runs the ``staleness.review_signals`` detector over the *eligible* active
    memories (see that module for the signal set and gating). Purely
    informational — nothing is demoted or deleted; the caller decides whether
    to ``memory_update`` (reconfirm/correct) or ``memory_forget`` each hit.

    The SQL prefilter keeps this cheap on the memory_stats hot path: gated
    signals can only fire once the confirmation anchor is past the review
    window, and the ungated signals require the literal substrings
    "expire"/"as of" — so recently-confirmed memories without those markers
    are excluded before any content leaves SQLite.
    """
    and_ns = " AND namespace_id = ?" if namespace_id else ""
    ns_params: tuple = (namespace_id,) if namespace_id else ()
    cutoff = _cutoff_iso(REVIEW_HINT_AFTER_DAYS)
    rows = conn.execute(
        "SELECT id, title, content, last_confirmed, updated_at, created_at "
        "FROM memories WHERE confidence != 'deprecated'" + and_ns + " "
        "AND (COALESCE(last_confirmed, updated_at, created_at) < ? "
        "OR content LIKE '%expire%' OR content LIKE '%as of%')",
        (*ns_params, cutoff),
    ).fetchall()

    flagged = []
    for row in rows:
        signals = review_signals(
            row["content"],
            last_confirmed=row["last_confirmed"],
            updated_at=row["updated_at"],
            created_at=row["created_at"],
        )
        if signals:
            flagged.append({"id": row["id"], "title": row["title"], "signals": signals})

    return {
        "review_suggested": len(flagged),
        "sample": flagged[:_REVIEW_SAMPLE_LIMIT],
    }


# Cap on how many duplicate-title clusters we surface in the stats sample.
# The full count is always reported; the sample is just for human inspection.
_HYGIENE_SAMPLE_LIMIT = 5


def compute_hygiene(conn: sqlite3.Connection, *, namespace_id: str | None = None) -> dict:
    """Cheap, SQL-only hygiene signals for catching cleanup candidates.

    Surfaces three things the manual namespace-scan workflow looks for first:

    * ``ghost_namespaces`` — namespaces with zero memories (skipped when a
      ``namespace_id`` filter is applied, since the scope is a single ns).
    * ``duplicate_title_count`` — number of (namespace, title) pairs that
      appear in 2+ active memories. A strong signal of literal duplication.
    * ``duplicate_title_sample`` — up to ``_HYGIENE_SAMPLE_LIMIT`` of those
      clusters with their memory ids, so the caller can inspect or merge.

    Semantic near-duplicate detection is intentionally NOT done here — the
    N² comparisons would be too expensive for a stats call. Stores get a
    semantic hint via ``memory_store``'s ``similar_memories``.
    """
    ghost_namespaces: list[str] = []
    if namespace_id is None:
        ghost_namespaces = [
            row["name"]
            for row in conn.execute(
                "SELECT n.name AS name FROM namespaces n "
                "LEFT JOIN memories m ON m.namespace_id = n.id "
                "GROUP BY n.id HAVING COUNT(m.id) = 0 ORDER BY n.name"
            ).fetchall()
        ]

    and_ns = " AND namespace_id = ?" if namespace_id else ""
    ns_params: tuple = (namespace_id,) if namespace_id else ()

    clusters = conn.execute(
        "SELECT namespace_id, title, GROUP_CONCAT(id) AS ids, COUNT(*) AS n "
        "FROM memories WHERE confidence != 'deprecated'" + and_ns + " "
        "GROUP BY namespace_id, title HAVING n > 1 "
        "ORDER BY n DESC, title ASC",
        ns_params,
    ).fetchall()

    ns_names = {
        row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM namespaces").fetchall()
    }

    sample = [
        {
            "namespace": ns_names.get(row["namespace_id"], "?"),
            "title": row["title"],
            "ids": row["ids"].split(","),
        }
        for row in clusters[:_HYGIENE_SAMPLE_LIMIT]
    ]

    return {
        "ghost_namespaces": ghost_namespaces,
        "duplicate_title_count": len(clusters),
        "duplicate_title_sample": sample,
    }


def _credential_health(conn: sqlite3.Connection) -> dict:
    """Credential expiry summary (no keychain access)."""
    from .credentials import CredentialVault

    return CredentialVault(conn).health()
