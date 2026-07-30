"""Wiring between the write path and extracted state claims.

Kept out of ``storage.py`` deliberately: that module is already well past the
300-line limit and this is a separable concern — deriving a memory's claims
from its text, and asking what those claims contradict.

Everything here is best-effort by design. A claim is an advisory hint; a
failure to extract one must never take down a store or an update, so the
callers swallow and log rather than raise.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from . import claims as claims_mod
from .models import Memory, utcnow_iso

logger = logging.getLogger(__name__)


def namespace_default_repo(conn: sqlite3.Connection, namespace_id: str) -> str | None:
    """The repo a bare "PR #12" means in this namespace.

    The one-namespace-per-repo convention makes that the namespace's own name.
    Returns None when the namespace is missing so bare refs get dropped rather
    than mis-keyed — see ``claims`` for why dropping beats guessing.
    """
    row = conn.execute("SELECT name FROM namespaces WHERE id = ?", (namespace_id,)).fetchone()
    return row["name"] if row else None


def sync(conn: sqlite3.Connection, mem: Memory, now: str) -> None:
    """Re-derive a memory's state claims from its current text."""
    try:
        extracted = claims_mod.extract_claims(
            mem.title,
            mem.content,
            namespace_default=namespace_default_repo(conn, mem.namespace_id),
        )
        sync_claims(conn, mem.id, extracted, now=now)
    except Exception:  # noqa: BLE001 - never fail a write over a hint
        logger.warning("claim extraction failed for %s", mem.id, exc_info=True)


def contradicted(conn: sqlite3.Connection, mem: Memory) -> list[dict]:
    """Older memories whose open claim ``mem`` has just resolved.

    The write-time hook. When a memory records "PR #10 merged", every memory
    still asserting "PR #10 open" is knowable *right now* — which is when
    reconciling is cheapest, because the caller is already thinking about that
    exact PR. Measured on a real 764-memory corpus, 10 stale PR claims had
    their resolution already sitting in the brain, written later and unlinked.
    """
    try:
        extracted = claims_mod.extract_claims(
            mem.title,
            mem.content,
            namespace_default=namespace_default_repo(conn, mem.namespace_id),
        )
        return find_contradicted(
            conn,
            namespace_id=mem.namespace_id,
            claims=extracted,
            exclude_memory_id=mem.id,
        )
    except Exception:  # noqa: BLE001 - a hint must never break a write
        logger.warning("contradiction lookup failed for %s", mem.id, exc_info=True)
        return []


def resolve(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    refs: list[str],
    resolved_by: str | None = None,
) -> list[str]:
    """Mark open claims resolved, leaving the memory's prose untouched.

    This is the path that makes the whole design work. A session log that said
    "PR #10 open" was correct on the day it was written; rewriting it to stay
    current destroys an accurate record. Here the body stays byte-identical and
    only the claim's resolution is recorded.
    """
    updated = resolve_refs(
        conn,
        memory_id=memory_id,
        refs=refs,
        resolved_by=resolved_by,
        now=utcnow_iso(),
    )
    conn.commit()
    return updated


# --- persistence ------------------------------------------------------------


def sync_claims(
    conn: sqlite3.Connection,
    memory_id: str,
    claims: list[claims_mod.Claim],
    *,
    now: str,
) -> int:
    """Replace a memory's claim rows with ``claims``. Returns the count written.

    Called on every store and on any title/content update, since the claims a
    memory makes are derived from its text. Resolution state is deliberately
    NOT preserved across a re-sync: if the text changed, what it asserts may
    have changed too, and a stale resolution pointer would be worse than none.
    """
    conn.execute("DELETE FROM memory_claims WHERE memory_id = ?", (memory_id,))
    for claim in claims:
        conn.execute(
            "INSERT INTO memory_claims "
            "(id, memory_id, kind, ref, state, evidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                memory_id,
                claim.kind,
                claim.ref,
                claim.state,
                claim.evidence,
                now,
            ),
        )
    return len(claims)


def find_contradicted(
    conn: sqlite3.Connection,
    *,
    namespace_id: str,
    claims: list[claims_mod.Claim],
    exclude_memory_id: str | None = None,
) -> list[dict]:
    """Memories whose open claim is contradicted by a resolved claim in ``claims``.

    This is the write-time hook: the moment a memory records "PR #10 merged",
    every older memory still asserting "PR #10 open" is knowable. Restricted to
    ``namespace_id`` so a bare-ref mis-key in one namespace cannot reach across
    into another.

    Advisory only — nothing is mutated. The caller decides whether to reconcile.
    """
    resolved = [c for c in claims if c.state == claims_mod.STATE_RESOLVED]
    if not resolved:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for claim in resolved:
        rows = conn.execute(
            "SELECT c.memory_id, c.ref, c.evidence, m.title, m.created_at "
            "FROM memory_claims c JOIN memories m ON m.id = c.memory_id "
            "WHERE c.kind = ? AND c.ref = ? AND c.state = ? "
            "AND c.resolved_at IS NULL AND m.namespace_id = ? "
            "AND m.confidence != 'deprecated'",
            (claim.kind, claim.ref, claims_mod.STATE_OPEN, namespace_id),
        ).fetchall()
        for row in rows:
            if row["memory_id"] == exclude_memory_id or row["memory_id"] in seen:
                continue
            seen.add(row["memory_id"])
            out.append(
                {
                    "id": row["memory_id"],
                    "title": row["title"],
                    "ref": claim.ref,
                    "asserts": claims_mod.STATE_OPEN,
                    "now": claims_mod.STATE_RESOLVED,
                    "their_evidence": row["evidence"],
                    "our_evidence": claim.evidence,
                }
            )
    return out


def resolve_refs(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    refs: list[str],
    resolved_by: str | None,
    now: str,
) -> list[str]:
    """Mark specific open claims resolved. Returns the refs actually updated.

    ``refs`` may contain the literal ``"all"`` to resolve every open claim on
    the memory — the common case when reconciling a dated record whose whole
    set of in-flight references has since landed.
    """
    if any(r.strip().lower() == "all" for r in refs):
        targets = [
            row["ref"]
            for row in conn.execute(
                "SELECT ref FROM memory_claims "
                "WHERE memory_id = ? AND state = ? AND resolved_at IS NULL",
                (memory_id, claims_mod.STATE_OPEN),
            )
        ]
    else:
        targets = [r.strip() for r in refs if r.strip()]
    return [
        ref
        for ref in targets
        if mark_resolved(conn, memory_id=memory_id, ref=ref, resolved_by=resolved_by, now=now)
    ]


def mark_resolved(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    ref: str,
    resolved_by: str | None,
    now: str,
) -> bool:
    """Record that a memory's open claim about ``ref`` is resolved.

    The memory's PROSE IS NEVER TOUCHED. It said "open" and that was true when
    written; this records what we learned later. That separation is the reason
    this table exists instead of a ``=== STATUS ===`` banner convention.
    """
    cur = conn.execute(
        "UPDATE memory_claims SET resolved_state = ?, resolved_by = ?, resolved_at = ? "
        "WHERE memory_id = ? AND ref = ? AND resolved_at IS NULL",
        (claims_mod.STATE_RESOLVED, resolved_by, now, memory_id, ref),
    )
    return cur.rowcount > 0


# --- stats -------------------------------------------------------------------

# Mirrors the review-sample caps in stats.py: report the full count always,
# and let a sweep raise the sample to enumerate the whole backlog.
_SAMPLE_LIMIT = 5
_SAMPLE_MAX = 100


def claim_stats(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
    sample_limit: int | None = None,
) -> dict:
    """State-claim health, and the reconciliation backlog.

    ``contradicted`` is the number that matters: memories still asserting a
    ref is open when a later memory in the same namespace says it resolved.
    That is a real backlog, not a heuristic nudge — both sides are recorded
    claims, so a hit means the brain already holds the answer and nobody
    joined the two.

    Pairs with ``memory_search(contradicted=True)`` to pull the full bodies,
    then ``memory_update(resolve_claims=...)`` to reconcile without touching
    a single character of prose.
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
        "SELECT DISTINCT c.memory_id AS id, c.ref AS ref, m.title AS title "
        "FROM memory_claims c JOIN memories m ON m.id = c.memory_id "
        "WHERE c.state = 'open' AND c.resolved_at IS NULL "
        f"AND m.confidence != 'deprecated'{and_ns} "
        "AND EXISTS (SELECT 1 FROM memory_claims o "
        "            JOIN memories om ON om.id = o.memory_id "
        "            WHERE o.kind = c.kind AND o.ref = c.ref "
        "              AND o.state = 'resolved' AND o.memory_id != c.memory_id "
        "              AND om.namespace_id = m.namespace_id "
        "              AND om.confidence != 'deprecated') "
        "ORDER BY m.created_at",
        ns_params,
    ).fetchall()
    return {
        "open": by_state.get("open", 0),
        "resolved": by_state.get("resolved", 0),
        "contradicted": len(rows),
        "sample": [dict(r) for r in rows[:limit]],
    }
