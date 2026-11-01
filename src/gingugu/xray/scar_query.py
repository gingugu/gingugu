"""X3 scar tissue, the brain's half: which memories in a namespace name a file.

Runs where the database is - in-process for a local brain, behind
``/hook/scars`` for a remote one. It never sees a checkout: given a namespace
and a file's basename it FTS-searches for the name and sends back, per memory,
its id, type, title, age and every path-like token it names. ``scars`` (the
client) resolves those tokens against the files it has and does the scoring.

Only the kinds of memory that record a cost are asked for; workflow and context
notes name files in passing, by the hundred.
"""

from __future__ import annotations

import re
import sqlite3

SCAR_TYPES = ("bug", "decision", "architecture", "fact", "pattern")
MAX_HITS = 200
TITLE_CHARS = 200
MAX_PATHS = 64
# FTS matches the name as a phrase ("storage py"), so prose can match too; the
# token check after it is the real test. Read past that many to fill MAX_HITS.
_FTS_POOL = 1000

_EXTS = (
    "py pyi ts tsx mts cts js jsx mjs cjs go rs rb php lua luau "
    "md toml json yml yaml sh sql html css cfg ini txt"
).split()
# A path: optional ./, then segments of word characters, dots and dashes, then a
# known extension. Not preceded or followed by more path, so a version
# (0.40.0), a host (gingugu.com) or "e.g." never reads as one.
_TOKEN = re.compile(
    r"(?<![\w./-])(?:\./)?((?:[\w][\w.-]*/)*[\w][\w-]*(?:\.[\w-]+)*\.(?:"
    + "|".join(sorted(_EXTS, key=len, reverse=True))
    + r"))(?![\w/-])"
)
_BASENAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,92}\.[A-Za-z0-9]{1,6}")

_SQL = """
SELECT m.id, m.type, m.title, m.content, m.created_at
  FROM memories_fts f
  JOIN memories m   ON m.rowid = f.rowid
  JOIN namespaces n ON n.id = m.namespace_id
 WHERE memories_fts MATCH ?
   AND n.name = ?
   AND m.type IN ({types})
   AND m.confidence != 'deprecated'
 ORDER BY m.created_at DESC, m.id
 LIMIT ?
"""


def valid_basename(name: object) -> bool:
    """A plain file name with an extension: what may be quoted into FTS."""
    return isinstance(name, str) and ".." not in name and bool(_BASENAME.fullmatch(name))


def path_tokens(text: str) -> list[str]:
    """Path-like tokens in ``text``, first seen first, at most ``MAX_PATHS``."""
    out: list[str] = []
    for match in _TOKEN.finditer(text):
        token = match.group(1)
        if token not in out:
            out.append(token)
            if len(out) == MAX_PATHS:
                break
    return out


def scars(conn: sqlite3.Connection, namespace: str, basename: str) -> list[dict]:
    """Memories in ``namespace`` that name a file called ``basename``, newest first."""
    if not valid_basename(basename):
        return []
    sql = _SQL.format(types=",".join("?" * len(SCAR_TYPES)))
    try:
        rows = conn.execute(sql, (f'"{basename}"', namespace, *SCAR_TYPES, _FTS_POOL)).fetchall()
    except sqlite3.OperationalError:
        return []  # FTS syntax is expected input, never a crash
    out = []
    for mid, mtype, title, content, created in rows:
        paths = path_tokens(f"{title}\n{content}")
        if not any(p.rsplit("/", 1)[-1] == basename for p in paths):
            continue
        out.append(
            {
                "id": mid,
                "type": mtype,
                "title": (title or "")[:TITLE_CHARS],
                "created_at": created or "",
                "paths": paths,
            }
        )
        if len(out) >= MAX_HITS:
            break
    return out
