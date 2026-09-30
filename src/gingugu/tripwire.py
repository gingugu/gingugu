"""Tripwires: match a pending tool call against memories that guard it.

A tripwire is two regexes bound to a memory - one over the tool's name, one
over its input. Matching is plain ``re``, never similarity: a risky command and
a harmless one can read alike to an encoder, and a pattern the user wrote says
exactly what it means. That keeps the store's standing rule that what surfaces
is calculated, never judged.

This module is the read side the hook needs plus the CRUD the MCP tool needs.
Like ``query_log``, nothing here commits: callers own the transaction.

``load_tripwires`` is the seam for a remote brain. Today it reads SQLite; once
the brain lives behind ``gingugu serve``, only that function has to learn to
ask over the network.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass

from .models import utcnow_iso

# Long enough for any real command pattern; short enough that a pasted blob is
# rejected rather than compiled on every tool call.
MAX_PATTERN_CHARS = 500

# A deny reason is read before every retry. Three memories is a warning; more
# is a wall of text that trains the reader to skip it.
MAX_TRIPPED = 3

SUMMARY_CHARS = 200

# Tools whose input is best described by one field. Anything else is matched
# against its whole input as stable JSON.
_INPUT_FIELD = {
    "Bash": "command",
    "Edit": "file_path",
    "Write": "file_path",
    "Read": "file_path",
    "NotebookEdit": "notebook_path",
}

_LOAD_SQL = """
SELECT t.id, t.memory_id, t.tool_pattern, t.input_pattern,
       m.title, m.content, n.name, m.type
  FROM tripwires t
  JOIN memories m   ON m.id = t.memory_id
  JOIN namespaces n ON n.id = m.namespace_id
 WHERE n.name IN ({placeholders})
   AND m.confidence != 'deprecated'
   AND m.id NOT IN (SELECT target_id FROM relations WHERE relation_type = 'supersedes')
 ORDER BY t.created_at, t.id
"""

_LIST_SQL = """
SELECT t.id, t.memory_id, t.tool_pattern, t.input_pattern, t.created_at,
       m.title, n.name AS namespace
  FROM tripwires t
  JOIN memories m   ON m.id = t.memory_id
  JOIN namespaces n ON n.id = m.namespace_id
"""


@dataclass(frozen=True)
class Tripwire:
    """One trigger, joined to the memory it speaks for."""

    id: str
    memory_id: str
    tool_pattern: str
    input_pattern: str
    title: str
    summary: str
    namespace: str
    type: str


def match_text(tool_name: str, tool_input: object) -> str:
    """The string an ``input_pattern`` is searched in."""
    if not isinstance(tool_input, dict):
        return ""
    field = _INPUT_FIELD.get(tool_name)
    if field is not None:
        return str(tool_input.get(field) or "")
    return json.dumps(tool_input, sort_keys=True, default=str)


def validate_patterns(tool_pattern: str, input_pattern: str) -> str | None:
    """An error message, or None when both patterns are usable."""
    for label, pattern in (("tool", tool_pattern), ("pattern", input_pattern)):
        if not pattern or not pattern.strip():
            return f"{label} must not be empty"
        if len(pattern) > MAX_PATTERN_CHARS:
            return f"{label} is longer than {MAX_PATTERN_CHARS} characters"
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"{label} is not a valid regex: {exc}"
    return None


def load_tripwires(conn: sqlite3.Connection, namespaces: list[str]) -> list[Tripwire]:
    """Every live trigger in scope.

    Deprecated and superseded memories are skipped for the same reason the
    prompt sweep skips them: the store has recorded them as no longer true.
    Pinned ones are NOT skipped. A pin loaded at session start is not the same
    as a pin in front of the agent at the moment it acts.
    """
    if not namespaces:
        return []
    marks = ",".join("?" * len(namespaces))
    rows = conn.execute(_LOAD_SQL.format(placeholders=marks), namespaces).fetchall()
    return [
        Tripwire(
            id=r[0],
            memory_id=r[1],
            tool_pattern=r[2],
            input_pattern=r[3],
            title=r[4],
            summary=" ".join((r[5] or "").split())[:SUMMARY_CHARS],
            namespace=r[6],
            type=r[7],
        )
        for r in rows
    ]


def matching(wires: list[Tripwire], tool_name: str, tool_input: object) -> list[Tripwire]:
    """The triggers this call trips, one per memory, capped.

    ``tool_pattern`` must match the WHOLE name, so ``Bash`` does not catch
    ``BashOutput``; ``input_pattern`` may match anywhere. A stored pattern that
    no longer compiles is skipped: a hook must never fail on the store's data.
    """
    text = match_text(tool_name, tool_input)
    hits: list[Tripwire] = []
    seen: set[str] = set()
    for wire in wires:
        if wire.memory_id in seen:
            continue
        try:
            if not re.fullmatch(wire.tool_pattern, tool_name):
                continue
            if not re.search(wire.input_pattern, text):
                continue
        except re.error:
            continue
        hits.append(wire)
        seen.add(wire.memory_id)
        if len(hits) >= MAX_TRIPPED:
            break
    return hits


def render_reason(hits: list[Tripwire]) -> str:
    """The deny reason: what the memory says, and how to get past it."""
    lines = ["GINGUGU TRIPWIRE: this call matches a memory you asked to be stopped at."]
    for hit in hits:
        lines.append(f"- [{hit.namespace}/{hit.type}] {hit.title}")
        if hit.summary:
            lines.append(f"  {hit.summary}")
        lines.append(f"  id={hit.memory_id} tripwire={hit.id}")
    lines.append(
        "Read it before going on. If the call still stands as intended, re-issue it "
        "unchanged: a tripwire fires once per session and will let it through."
    )
    return "\n".join(lines)


def add_tripwire(
    conn: sqlite3.Connection, memory_id: str, tool_pattern: str, input_pattern: str
) -> dict:
    """Bind a trigger to a memory. Raises ``ValueError`` on bad input."""
    problem = validate_patterns(tool_pattern, input_pattern)
    if problem:
        raise ValueError(problem)
    if conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone() is None:
        raise ValueError(f"memory {memory_id!r} not found")
    row = {
        "id": str(uuid.uuid4()),
        "memory_id": memory_id,
        "tool_pattern": tool_pattern,
        "input_pattern": input_pattern,
        "created_at": utcnow_iso(),
    }
    conn.execute(
        "INSERT INTO tripwires(id, memory_id, tool_pattern, input_pattern, created_at) "
        "VALUES (:id, :memory_id, :tool_pattern, :input_pattern, :created_at)",
        row,
    )
    return row


def list_tripwires(
    conn: sqlite3.Connection,
    *,
    memory_id: str | None = None,
    namespaces: list[str] | None = None,
) -> list[dict]:
    """Triggers with their memory's title and namespace, oldest first."""
    clauses: list[str] = []
    params: list[str] = []
    if memory_id:
        clauses.append("t.memory_id = ?")
        params.append(memory_id)
    if namespaces:
        clauses.append(f"n.name IN ({','.join('?' * len(namespaces))})")
        params.extend(namespaces)
    sql = _LIST_SQL + (" WHERE " + " AND ".join(clauses) if clauses else "")
    sql += " ORDER BY t.created_at, t.id"
    cols = ("id", "memory_id", "tool_pattern", "input_pattern", "created_at", "title", "namespace")
    return [dict(zip(cols, r, strict=True)) for r in conn.execute(sql, params).fetchall()]


def remove_tripwire(conn: sqlite3.Connection, tripwire_id: str) -> bool:
    return conn.execute("DELETE FROM tripwires WHERE id = ?", (tripwire_id,)).rowcount > 0
