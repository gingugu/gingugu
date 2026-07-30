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

from . import claims as claims_mod
from .models import Memory

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
        claims_mod.sync_claims(conn, mem.id, extracted, now=now)
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
        return claims_mod.find_contradicted(
            conn,
            namespace_id=mem.namespace_id,
            claims=extracted,
            exclude_memory_id=mem.id,
        )
    except Exception:  # noqa: BLE001 - a hint must never break a write
        logger.warning("contradiction lookup failed for %s", mem.id, exc_info=True)
        return []
