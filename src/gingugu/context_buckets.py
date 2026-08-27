"""Where ``memory_context``'s buckets come from.

One function per intent, each ranked by its own native signal in SQL. Kept
apart from ``context.py`` (which decides how the buckets are combined,
quota'd and presented) because the two answer different questions and the
combined module had outgrown the repo's size discipline.

Each function returns rows already ordered by the signal that bucket exists
to serve. Nothing here scores, de-duplicates or truncates against another
bucket - that is the caller's job.
"""

from __future__ import annotations

import sqlite3

from .models import Memory, memory_columns_sql

# Hard ceiling on pinned memories loaded per namespace. Pins bypass ranking
# entirely, so this is the only thing bounding their context cost. It is a
# safety limit, not a target. A tier this size stays scannable at a glance;
# past it, pinning has degraded into a second unranked pile and the right fix
# is to unpin, not to raise the cap.
PINNED_HARD_CAP = 20

_COLUMNS = memory_columns_sql()


def recently_active(conn: sqlite3.Connection, namespace_id: str, limit: int) -> list[Memory]:
    """Most recently *written* memories, excluding this namespace's pins.

    Ordered by ``updated_at``, a write timestamp, because this bucket exists so
    that a freshly-stored, never-read memory survives the cut - the "where we
    left off" signal. It used to order by ``last_accessed``, which is a *read*
    timestamp, and so answered the opposite question: not "is this new to me?"
    but "have I seen this lately?". The two are anti-correlated by definition,
    and the bucket whose whole job is discovery was ranked by familiarity.

    Reading never moves ``updated_at``, which is what makes it safe here. Of the
    three statements that write this table, two touch only ``last_accessed``
    (``record_accesses`` and ``touch_many``) and the third is an explicit edit.
    That matters because ``memory_context`` itself touches everything it
    surfaces: on the old sort key each session-start load promoted its own
    output into the next load's bucket, so the bucket converged on what it had
    already shown and newly-stored memories could never break in.

    ``last_accessed`` is untouched and still correct for what it is actually
    for - dormancy and the access signal. The two questions get two columns
    rather than one column meaning both things.

    Pins are filtered in SQL rather than afterwards in Python because ``LIMIT``
    applies first: fetching N rows and *then* dropping the pinned ones yields
    fewer than N ranked candidates, so a full pin tier would quietly starve the
    recency bucket it was supposed to sit alongside.

    ``rowid`` breaks ties, descending. Timestamps are only as fine as the clock
    that made them - Windows resolves ``datetime.now()`` to 15.6ms before Python
    3.13 - so two memories written in the same tick carry a byte-identical
    ``updated_at``. Left unspecified, SQLite settles that in rowid *ascending*
    order and hands back the older one first, which is the very inversion this
    ordering exists to prevent, just at sub-tick scale. ``rowid`` is insertion
    order on a rowid table, so descending is "last written wins" - the same rule
    the timestamp expresses, applied where the timestamp has run out of digits.
    """
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM memories "
        "WHERE namespace_id = ? AND confidence != 'deprecated' AND pinned = 0 "
        "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
        (namespace_id, limit),
    ).fetchall()
    return [Memory(**dict(r)) for r in rows]


def pinned(conn: sqlite3.Connection, namespace_id: str, limit: int) -> list[Memory]:
    """Pinned memories for a namespace, newest-confirmed first.

    Deprecated memories are excluded: a pin says "never let me miss this", and
    deprecating a memory says "this is no longer true". The latter wins: the
    pin is simply ignored until someone unpins or re-verifies it.

    Ordering only decides who survives ``PINNED_HARD_CAP``, so it favours the
    most recently reconfirmed. ``COALESCE`` keeps never-confirmed pins ordered
    by creation instead of sorting them last under NULL.
    """
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM memories "
        "WHERE namespace_id = ? AND pinned = 1 AND confidence != 'deprecated' "
        "ORDER BY COALESCE(last_confirmed, created_at) DESC LIMIT ?",
        (namespace_id, limit),
    ).fetchall()
    return [Memory(**dict(r)) for r in rows]


def cross_namespace_patterns(
    conn: sqlite3.Connection, exclude_ns: str, limit: int = 3
) -> list[Memory]:
    """Verified patterns/preferences from *other* namespaces, by access count."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM memories "
        "WHERE type IN ('pattern', 'preference') AND confidence = 'verified' "
        "AND namespace_id != ? "
        "ORDER BY access_count DESC LIMIT ?",
        (exclude_ns, limit),
    ).fetchall()
    return [Memory(**dict(r)) for r in rows]
