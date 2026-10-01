"""Health metrics (``memory_stats``) and opportunistic access-log pruning."""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta

from . import claim_queries, grants
from .decay import DEPRECATE_SUGGEST_AFTER_DAYS, DORMANT_AFTER_DAYS
from .graph_stats import compute_graph
from .hygiene_stats import compute_hygiene
from .size_stats import compute_size
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


def compute_global_stats(conn: sqlite3.Connection) -> dict:
    """The namespace-independent fields: inventory, access-log and query-log
    volume, and credential health.

    Split out of ``compute_stats`` so a multi-namespace ``memory_stats`` call
    can compute this once and reuse it across every namespace's block, instead
    of repeating a namespace-independent computation once per namespace.
    Prunes the access log as a side effect (throttled - see
    ``prune_access_log``), so a caller building several namespace blocks should
    call this exactly once per ``memory_stats`` call.
    """
    prune_access_log(conn)
    namespaces = [
        {"name": row["name"], "count": row["n"]}
        for row in conn.execute(
            "SELECT n.name AS name, COUNT(m.id) AS n FROM namespaces n "
            "LEFT JOIN memories m ON m.namespace_id = n.id "
            "GROUP BY n.id ORDER BY n DESC"
        ).fetchall()
        if grants.can_read_name(row["name"])
    ]
    if grants.current() is not None:
        # A scoped token sees its own namespaces' inventory and nothing
        # store-wide: log volume and vault health describe every namespace.
        return {"namespaces": namespaces}
    return {
        "namespaces": namespaces,
        "access_log_rows": _count(conn, "SELECT COUNT(*) FROM access_log"),
        "query_log_rows": _count(conn, "SELECT COUNT(*) FROM query_log"),
        "credentials": _credential_health(conn),
    }


def _compute_namespace_stats(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
    review_limit: int | None = None,
) -> dict:
    """Everything ``compute_stats`` returns *except* the four global fields
    (``namespaces``, ``access_log_rows``, ``query_log_rows``, ``credentials``) -
    see ``compute_global_stats``. Scoped to ``namespace_id`` when given."""
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

    size = compute_size(conn, ns_clause, ns_params)

    return {
        "total_memories": total,
        "by_type": by_type,
        "by_confidence": by_confidence,
        "dormant_count": dormant_count,
        # Back-compat alias for older consumers; dormancy supersedes staleness.
        "stale_count": dormant_count,
        "deprecation_suggested": deprecate_suggest,
        "size": size,
        "graph": compute_graph(conn, namespace_id=namespace_id, sample_limit=review_limit),
        "hygiene": compute_hygiene(conn, namespace_id=namespace_id),
        "review": compute_review(conn, namespace_id=namespace_id, sample_limit=review_limit),
        "claims": claim_queries.claim_stats(
            conn, namespace_id=namespace_id, sample_limit=review_limit
        ),
    }


def compute_stats(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
    review_limit: int | None = None,
) -> dict:
    """Health overview: counts, staleness, and per-type/confidence breakdowns.

    Includes the four namespace-independent fields (``namespaces``,
    ``access_log_rows``, ``query_log_rows``, ``credentials`` - see
    ``compute_global_stats``) alongside the namespace-scoped ones, exactly as
    it always has: this is the single-namespace and unscoped shape
    memory_stats returns under ``stats``.
    A multi-namespace call instead calls ``compute_global_stats`` once and
    ``_compute_namespace_stats`` per namespace, so the global fields are never
    recomputed once per namespace.
    """
    return {
        **compute_global_stats(conn),
        **_compute_namespace_stats(conn, namespace_id=namespace_id, review_limit=review_limit),
    }


# Default cap on how many review-flagged memories we list in the stats sample;
# the full count is always reported. Callers raise it (up to the max) to
# enumerate every flagged memory for a reconciliation sweep.
_REVIEW_SAMPLE_LIMIT = 5
_REVIEW_SAMPLE_MAX = 100


def compute_review(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
    sample_limit: int | None = None,
) -> dict:
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
    limit = _REVIEW_SAMPLE_LIMIT if sample_limit is None else sample_limit
    limit = max(1, min(limit, _REVIEW_SAMPLE_MAX))
    and_ns = " AND namespace_id = ?" if namespace_id else ""
    ns_params: tuple = (namespace_id,) if namespace_id else ()
    cutoff = _cutoff_iso(REVIEW_HINT_AFTER_DAYS)
    rows = conn.execute(
        "SELECT id, type, title, content, last_confirmed, updated_at, created_at "
        "FROM memories WHERE confidence != 'deprecated'" + and_ns + " "
        # Freshness anchor — the LATEST of the three, matching
        # decay.reference_timestamp (only last_confirmed is nullable).
        "AND (MAX(COALESCE(last_confirmed, ''), updated_at, created_at) < ? "
        "OR content LIKE '%expire%' OR content LIKE '%as of%')",
        (*ns_params, cutoff),
    ).fetchall()

    flagged = []
    for row in rows:
        signals = review_signals(
            row["content"],
            memory_type=row["type"],
            last_confirmed=row["last_confirmed"],
            updated_at=row["updated_at"],
            created_at=row["created_at"],
        )
        if signals:
            flagged.append({"id": row["id"], "title": row["title"], "signals": signals})

    return {
        "review_suggested": len(flagged),
        "sample": flagged[:limit],
    }


def _credential_health(conn: sqlite3.Connection) -> dict:
    """Credential expiry summary (no keychain access)."""
    from .credentials import CredentialVault

    return CredentialVault(conn).health()
