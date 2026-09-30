"""Migration 016: ``tripwires``, memories that fire at an action.

Involuntary recall on a prompt can only react to what the user typed. The
repeat mistakes that cost the most happen at a specific tool call instead, and
the prompt that led there rarely names it. A tripwire binds a memory to that
call: a pattern over the tool's name and one over its input.

A table of its own rather than a column: one memory can guard several calls,
and a trigger has a lifecycle (add, list, remove) of its own. ``ON DELETE
CASCADE`` means a hard-deleted memory cannot leave a trigger behind that fires
with nothing to say.
"""

from __future__ import annotations

import sqlite3

_SCHEMA_V16 = """
CREATE TABLE tripwires (
    id             TEXT PRIMARY KEY,
    memory_id      TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    tool_pattern   TEXT NOT NULL,
    input_pattern  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX idx_tripwires_memory ON tripwires(memory_id);
"""


def _migration_016_tripwires(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V16)
