"""Health metrics (``memory_stats``) and opportunistic access-log pruning."""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta

from .decay import DEPRECATE_SUGGEST_AFTER_DAYS, DORMANT_AFTER_DAYS

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
    }


def _credential_health(conn: sqlite3.Connection) -> dict:
    """Credential expiry summary (no keychain access)."""
    from .credentials import CredentialVault

    return CredentialVault(conn).health()
