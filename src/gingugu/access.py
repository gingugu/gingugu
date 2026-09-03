"""The ``access_log`` table and the two ways a memory can be touched.

Retrieval credits memories along two different axes, and conflating them is
what makes a ranking signal rot:

* ``record`` is a REAL access. It bumps ``access_count``, refreshes
  ``last_accessed``, and writes an ``access_log`` row. Only retrieval that
  actually returned a memory to a caller may use it.
* ``touch`` is REACTIVATION. It refreshes ``last_accessed`` and nothing else.
  Spreading activation wakes a memory's neighbours without any of them having
  been asked for, so counting those as accesses would let a well-connected
  memory inflate its own ranking by being adjacent to popular ones.

The same distinction is why a session-start context load calls ``touch``
rather than ``record``: a protocol-driven load is not evidence that anything
was relevant, and letting it count would make every memory look equally read.

Nothing here commits. Callers own the transaction boundary.
"""

from __future__ import annotations

import sqlite3
import uuid

from .models import utcnow_iso


def dedupe(memory_ids: list[str]) -> list[str]:
    """Drop blanks and repeats, preserving order.

    One call is one access even when a memory appears in it twice, so this runs
    before either write. ``dict.fromkeys`` rather than a set because the row
    order has to stay stable for the ``IN`` clause and for tests.
    """
    return list(dict.fromkeys(mid for mid in memory_ids if mid))


def record(conn: sqlite3.Connection, memory_ids: list[str], *, session_id: str | None) -> int:
    """Log a real access against each memory. Returns rows updated.

    Every row of one call shares a ``context``: the id of the MCP session that
    asked. That is the grouping key co-access analysis needs, and one call is
    the tightest honest bucket - these memories were returned together, by one
    query, to one client. It is NULL when no session is in flight; see
    ``session.current_session_id``.

    Expects ``memory_ids`` already through ``dedupe``.
    """
    now = utcnow_iso()
    conn.executemany(
        "INSERT INTO access_log(id, memory_id, accessed_at, context) VALUES (?, ?, ?, ?)",
        [(str(uuid.uuid4()), mid, now, session_id) for mid in memory_ids],
    )
    placeholders = ", ".join("?" for _ in memory_ids)
    cur = conn.execute(
        f"UPDATE memories SET access_count = access_count + 1, last_accessed = ? "
        f"WHERE id IN ({placeholders})",
        (now, *memory_ids),
    )
    return cur.rowcount


def touch(conn: sqlite3.Connection, memory_ids: list[str]) -> int:
    """Refresh ``last_accessed`` without counting an access. Returns rows updated.

    Expects ``memory_ids`` already through ``dedupe``.
    """
    now = utcnow_iso()
    placeholders = ", ".join("?" for _ in memory_ids)
    cur = conn.execute(
        f"UPDATE memories SET last_accessed = ? WHERE id IN ({placeholders})",
        (now, *memory_ids),
    )
    return cur.rowcount
