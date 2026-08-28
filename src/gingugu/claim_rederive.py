"""Corpus-wide claim re-derivation that preserves resolution state.

Distinct from ``claim_sync.sync_claims``, and the difference is the whole
reason this module exists. ``sync_claims`` runs when a memory's *text* changed,
so it deliberately drops resolution: if the prose moved, what it asserts may
have moved with it, and a stale ``resolved_by`` pointer is worse than none.

Here the text has not changed — the *extractor* got smarter. Blowing away
resolutions would destroy exactly the reconciliation work the claims feature
exists to capture, and that work is manual and unrecoverable. So this prunes
claims the current extractor no longer derives, refreshes the ones it still
does, and leaves ``resolved_state``/``resolved_by``/``resolved_at`` alone.

Positional row access throughout — migrations may hand us a connection with no
``row_factory`` set.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import sqlite3

logger = logging.getLogger(__name__)


def _default_repo(name: str, declared: str | None) -> str | None:
    """Mirror of ``claim_sync.namespace_default_repo`` for positional rows."""
    if declared is None:
        return name
    return declared or None


def rederive_claims(
    conn: sqlite3.Connection, *, namespace_id: str | None = None
) -> tuple[int, int]:
    """Re-derive memories' claims. Returns ``(pruned, written)``.

    Idempotent: running it twice prunes nothing the second time and rewrites
    the same rows. Safe to call from any migration that changes extraction.

    ``namespace_id`` scopes the sweep to one namespace. That is what makes a
    ``default_repo`` change take effect: claims are stored rows and the default
    is only consulted at extraction time, so without a re-derive the
    declaration changes nothing that already exists.
    """
    from . import claim_sync  # local import: no cycle at module scope
    from . import claims as claims_mod  # stdlib-only module; no import cycle

    sql = (
        "SELECT m.id, m.title, m.content, n.name, n.default_repo FROM memories m "
        "JOIN namespaces n ON n.id = m.namespace_id "
        "WHERE m.confidence != 'deprecated'"
    )
    params: tuple = ()
    if namespace_id is not None:
        sql += " AND m.namespace_id = ?"
        params = (namespace_id,)
    rows = conn.execute(sql, params).fetchall()
    now = datetime.now(UTC).isoformat()
    repos = claim_sync.known_repos(conn)
    pruned = written = 0

    for memory_id, title, content, ns_name, declared in rows:
        extracted = claims_mod.extract_claims(
            title or "",
            content or "",
            namespace_default=_default_repo(ns_name, declared),
            known_repos=repos,
        )
        pruned += _prune(conn, memory_id, {(c.kind, c.ref) for c in extracted})
        written += _upsert(conn, memory_id, extracted, now)

    logger.info(
        "Re-derived claims across %d memories: %d pruned, %d written", len(rows), pruned, written
    )
    return pruned, written


def _prune(conn: sqlite3.Connection, memory_id: str, keep: set[tuple[str, str]]) -> int:
    """Delete claim rows the current extractor no longer derives."""
    stale = [
        row[0]
        for row in conn.execute(
            "SELECT id, kind, ref FROM memory_claims WHERE memory_id = ?", (memory_id,)
        ).fetchall()
        if (row[1], row[2]) not in keep
    ]
    for claim_id in stale:
        conn.execute("DELETE FROM memory_claims WHERE id = ?", (claim_id,))
    return len(stale)


def _upsert(conn: sqlite3.Connection, memory_id: str, extracted: list, now: str) -> int:
    """Insert new claims; refresh state/evidence on existing ones.

    The ``UPDATE`` deliberately lists only ``state`` and ``evidence``. A claim
    whose asserted state was re-read from unchanged prose keeps whatever
    resolution a human recorded against it.
    """
    written = 0
    for claim in extracted:
        cursor = conn.execute(
            "UPDATE memory_claims SET state = ?, evidence = ? "
            "WHERE memory_id = ? AND kind = ? AND ref = ?",
            (claim.state, claim.evidence, memory_id, claim.kind, claim.ref),
        )
        if cursor.rowcount == 0:
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
        written += 1
    return written
