"""Read-only sweep that feeds the involuntary-recall gate.

Kept apart from ``recall_gate`` so the gate stays pure arithmetic over plain
dataclasses and can be tested without a database, a model, or a brain to point
at. Everything that touches the world lives here.

The connection is opened ``mode=ro`` deliberately. This code runs on the user's
keystroke, before their prompt is processed, on every single turn - it must be
incapable of migrating a schema, taking a write lock, or leaving a journal
behind next to a server that owns the file.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

from .recall_gate import Candidate, GateConfig, lexical_terms

# Long enough to summarise, short enough that three of them are cheap.
SUMMARY_CHARS = 200

# BM25 depth. The lexical half is a REQUIREMENT filter, not a ranker - it only
# has to answer "did this memory share subject matter with the prompt at all",
# so a generous pool costs nothing and avoids penalising a true hit that ranks
# low lexically because the prompt was wordy.
LEXICAL_POOL = 60

_SWEEP_SQL = """
SELECT m.id, m.title, m.content, m.type, n.name, e.embedding
  FROM memory_embeddings e
  JOIN memories m    ON m.id = e.memory_id
  JOIN namespaces n  ON n.id = m.namespace_id
 WHERE n.name IN ({placeholders})
   AND m.confidence != 'deprecated'
   AND m.pinned = 0
   AND m.id NOT IN (
        SELECT target_id FROM relations WHERE relation_type = 'supersedes'
   )
"""

_LEXICAL_SQL = """
SELECT m.id
  FROM memories_fts f
  JOIN memories m   ON m.rowid = f.rowid
  JOIN namespaces n ON n.id = m.namespace_id
 WHERE memories_fts MATCH ?
   AND n.name IN ({placeholders})
   AND m.confidence != 'deprecated'
 ORDER BY bm25(memories_fts)
 LIMIT ?
"""


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the brain read-only. Raises if the file is not there."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _cosine(query: list[float], vec: tuple[float, ...], query_norm: float) -> float:
    """Cosine, or 0.0 for anything not comparable.

    A stored vector of a different width came from a different model, so the
    two are not in the same space and the honest score is "no evidence". Left
    to `zip`'s default that mismatch would silently truncate to the shorter of
    the two and return a confident number about nothing.
    """
    if len(query) != len(vec):
        return 0.0
    dot = 0.0
    norm = 0.0
    for a, b in zip(query, vec, strict=True):
        dot += a * b
        norm += b * b
    if not norm or not query_norm:
        return 0.0
    return dot / (query_norm * norm**0.5)


def sweep(
    conn: sqlite3.Connection,
    query_vec: list[float],
    namespaces: list[str],
    *,
    config: GateConfig | None = None,
) -> list[Candidate]:
    """Score every eligible memory against ``query_vec``.

    Three exclusions are pushed into SQL rather than applied afterwards, each
    for its own reason:

    - ``deprecated`` is knowledge the user retired.
    - ``pinned = 0`` because pins already load unconditionally at session
      start. Injecting one spends context twice on a memory that is provably
      present, and crowds out the cap with something already read.
    - superseded memories, because a replaced memory injected as current
      states something the store itself has recorded as no longer true. The
      graph knows this, so the sweep should never have to guess.
    """
    cfg = config or GateConfig()
    if not namespaces:
        return []
    marks = ",".join("?" * len(namespaces))
    rows = conn.execute(_SWEEP_SQL.format(placeholders=marks), namespaces).fetchall()

    query_norm = sum(x * x for x in query_vec) ** 0.5
    out: list[Candidate] = []
    for mem_id, title, content, mtype, namespace, blob in rows:
        if mtype not in cfg.types:
            continue
        vec = struct.unpack(f"{len(blob) // 4}f", blob)
        score = _cosine(query_vec, vec, query_norm)
        summary = " ".join((content or "").split())[:SUMMARY_CHARS]
        out.append(
            Candidate(
                id=mem_id,
                title=title,
                summary=summary,
                namespace=namespace,
                type=mtype,
                similarity=score,
            )
        )
    return out


def lexical_matches(
    conn: sqlite3.Connection,
    prompt: str,
    namespaces: list[str],
    *,
    pool: int = LEXICAL_POOL,
) -> set[str]:
    """Ids a BM25 query over the prompt's terms also matched.

    Returns an empty set when the prompt yields no usable terms or FTS rejects
    the query. That is a real answer, not an error: with no lexical evidence
    the gate should decline rather than fall back to similarity alone, which is
    exactly the permissive behaviour the lexical half exists to remove.
    """
    terms = lexical_terms(prompt)
    if not terms or not namespaces:
        return set()
    match = " OR ".join(f'"{t}"' for t in terms)
    marks = ",".join("?" * len(namespaces))
    try:
        rows = conn.execute(
            _LEXICAL_SQL.format(placeholders=marks),
            (match, *namespaces, pool),
        ).fetchall()
    except sqlite3.OperationalError:
        # A prompt can contain anything; FTS5 syntax errors are expected input,
        # not bugs. Declining beats crashing on the user's keystroke.
        return set()
    return {r[0] for r in rows}
