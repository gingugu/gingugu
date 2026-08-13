"""Read-side queries over ``memory_claims`` — enumeration, filtering, stats.

Split from ``claim_sync`` (which owns the write path) for two reasons: that
module was past the repo's size limit, and the SQL here has a second consumer.
The contradiction predicate is not a stats-only concept — ``memory_search``
filters on the same condition — and two hand-written copies of a correlated
subquery is how they drift.

**Enumeration is the point.** The first version of this reported ``open: 5``
and then handed back a sample containing only the *contradicted* subset, so a
namespace could truthfully report five open claims and give the caller no way
to learn which five. Reconciling meant dropping to raw SQL against the live
database — which defeats the purpose of a memory server. ``sample`` now
enumerates every open claim, contradicted ones first, because the whole reason
claims are extracted is to be worked through and closed out.

**Contradiction stays inside one namespace.** ``claims.py`` keys a bare
"PR #12" off the namespace's default repo, so matching across namespaces would
pair two different repos' PR #12 and report a contradiction that does not
exist. A cross-namespace contradiction is therefore invisible here, on purpose:
a missed one is silent, a fabricated one teaches the reader to ignore the
metric. See the ``claims`` module docstring for the same argument applied to
extraction.
"""

from __future__ import annotations

import sqlite3

# Mirrors the review-sample caps in stats.py: report the full count always,
# and let a sweep raise the sample to enumerate the whole backlog.
_SAMPLE_LIMIT = 5
_SAMPLE_MAX = 100

CLAIM_FILTERS = ("open", "contradicted")

# A memory whose open claim some *other* memory in the same namespace has since
# recorded as resolved. Correlated against an outer ``memory_claims c``, so the
# caller supplies how to reach the owning memory's namespace.
_CONTRADICTS = (
    "SELECT 1 FROM memory_claims o JOIN memories om ON om.id = o.memory_id "
    "WHERE o.kind = c.kind AND o.ref = c.ref AND o.state = 'resolved' "
    "AND o.memory_id != c.memory_id AND om.namespace_id = {ns} "
    "AND om.confidence != 'deprecated'"
)


def claim_filter(alias: str, mode: str) -> str:
    """WHERE fragment keeping memories that carry open (or contradicted) claims.

    Takes no parameters — the states are literals, and ``alias`` names a table
    already in the caller's FROM. That keeps it composable with the FTS5 join,
    the embeddings join, and a plain table scan alike.
    """
    inner = (
        f"SELECT 1 FROM memory_claims c WHERE c.memory_id = {alias}.id "
        "AND c.state = 'open' AND c.resolved_at IS NULL"
    )
    if mode == "contradicted":
        inner += f" AND EXISTS ({_CONTRADICTS.format(ns=f'{alias}.namespace_id')})"
    return f"EXISTS ({inner})"


def claim_stats(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
    sample_limit: int | None = None,
) -> dict:
    """State-claim health, and the full reconciliation backlog.

    ``sample`` enumerates open claims — contradicted first, then oldest — each
    row carrying ``contradicted`` so the caller can tell the two apart without
    a second query. Raise ``memory_stats(review_limit=...)`` to list the whole
    backlog (max 100), then ``memory_search(ids=...)`` or
    ``memory_search(claims="open")`` to pull the bodies, then
    ``memory_update(resolve_claims=...)`` to close them out without touching a
    single character of prose.

    Two counts, deliberately: ``open`` is every unresolved open claim, while
    ``open_actionable`` excludes those on deprecated memories — which is what
    ``sample`` lists. Without the second number a caller comparing ``open`` to
    ``len(sample)`` finds a gap and no explanation for it.

    ``contradicted`` is the subset that matters most: the brain already holds
    the resolution, written later by another memory, and nobody joined the two.
    """
    limit = _SAMPLE_LIMIT if sample_limit is None else sample_limit
    limit = max(1, min(limit, _SAMPLE_MAX))
    and_ns = " AND m.namespace_id = ?" if namespace_id else ""
    ns_params: tuple = (namespace_id,) if namespace_id else ()

    by_state = {
        row["state"]: row["n"]
        for row in conn.execute(
            "SELECT c.state, COUNT(*) AS n FROM memory_claims c "
            "JOIN memories m ON m.id = c.memory_id "
            f"WHERE c.resolved_at IS NULL{and_ns} GROUP BY c.state",
            ns_params,
        )
    }
    rows = conn.execute(
        "SELECT DISTINCT c.memory_id AS id, m.title AS title, c.ref AS ref, "
        f"EXISTS ({_CONTRADICTS.format(ns='m.namespace_id')}) AS contradicted "
        "FROM memory_claims c JOIN memories m ON m.id = c.memory_id "
        "WHERE c.state = 'open' AND c.resolved_at IS NULL "
        f"AND m.confidence != 'deprecated'{and_ns} "
        "ORDER BY contradicted DESC, m.created_at",
        ns_params,
    ).fetchall()
    return {
        "open": by_state.get("open", 0),
        "open_actionable": len(rows),
        "resolved": by_state.get("resolved", 0),
        "contradicted": sum(1 for row in rows if row["contradicted"]),
        "sample": [
            {
                "id": row["id"],
                "title": row["title"],
                "ref": row["ref"],
                "contradicted": bool(row["contradicted"]),
            }
            for row in rows[:limit]
        ],
    }
