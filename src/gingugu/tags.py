"""The ``tags`` / ``memory_tags`` tables: the one place that writes them.

Extracted from ``storage.py`` for the same reason ``embedding_sync`` was: the
invariant belongs to whoever writes tag rows, and ``MemoryStore`` is not the
only writer. ``memory_import`` writes them too, and while this logic was
private to the store, portability carried a byte-identical private copy of
``get_or_create`` - the same drift class that once let a private column list
silently drop ``pinned``.

Nothing here commits. Callers own their transaction boundary, because a tag
write is almost always part of a larger memory write that must land atomically
with it.
"""

from __future__ import annotations

import sqlite3
import uuid

from .models import normalize_tag


def get_or_create(conn: sqlite3.Connection, name: str) -> str:
    """The id of the tag called ``name``, creating the row if it is new.

    Takes the name verbatim. Normalization is the caller's job because the
    import path replays names that were already normalized on the way out, and
    re-normalizing a stored tag is how a round trip starts changing data.
    """
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return row["id"]
    tag_id = str(uuid.uuid4())
    conn.execute("INSERT INTO tags(id, name) VALUES (?, ?)", (tag_id, name))
    return tag_id


def set_for(conn: sqlite3.Connection, memory_id: str, names: list[str]) -> list[str]:
    """Replace every tag on a memory with the normalized, de-duplicated set."""
    normalized = list(dict.fromkeys(normalize_tag(t) for t in names if t.strip()))
    conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory_id,))
    for name in normalized:
        tag_id = get_or_create(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO memory_tags(memory_id, tag_id) VALUES (?, ?)",
            (memory_id, tag_id),
        )
    prune_orphans(conn)
    return normalized


def add_to(conn: sqlite3.Connection, memory_id: str, names: list[str]) -> None:
    """Add tags to a memory without removing the ones already on it."""
    for name in (normalize_tag(t) for t in names if t.strip()):
        tag_id = get_or_create(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO memory_tags(memory_id, tag_id) VALUES (?, ?)",
            (memory_id, tag_id),
        )


def get_for(conn: sqlite3.Connection, memory_id: str) -> list[str]:
    """Every tag on a memory, ordered by name."""
    rows = conn.execute(
        "SELECT t.name FROM tags t "
        "JOIN memory_tags mt ON mt.tag_id = t.id "
        "WHERE mt.memory_id = ? ORDER BY t.name",
        (memory_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def prune_orphans(conn: sqlite3.Connection) -> None:
    """Drop tag rows no memory references, so the table cannot grow forever."""
    conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM memory_tags)")
