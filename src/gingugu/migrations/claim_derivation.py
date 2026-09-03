"""Claim migrations: the ones that derive or re-derive rows from existing prose.

Claims are *stored* rows extracted from memory text, so improving the extractor
changes nothing already on disk. Every fix to it therefore needs a migration
whose whole job is to re-read prose that never changed. Five of them exist for
that reason (005, 006, 007, 009, 010), and grouping them here keeps that shared
rationale in one place instead of repeating it down the version list.

Migrations that change the schema's shape live in ``schema``; the registry that
orders them all lives in this package's ``__init__``.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import UTC, datetime

from .. import claim_rederive

logger = logging.getLogger(__name__)

# --- Migration 005: extracted state claims ----------------------------------
#
# A memory that says "PR #10 is open" is making a CLAIM. That claim was true
# when written and goes silently wrong the moment the PR merges — but the
# prose is honest history and must never be edited to track it. So the claim
# lives here as data instead, keyed to something checkable.
#
# ``ref`` is repo-qualified ("gingugu#10"), because "PR #12" is not a global
# key — gingugu#12 and platform-infra#12 are different objects. A ref
# that cannot be qualified is NOT recorded: dropping beats guessing.
#
# ``state`` is what the memory ASSERTS, and is never rewritten. Resolution is
# recorded separately in ``resolved_*``, so the pair reads as "this memory
# claims X; we later learned Y" without touching a single character of prose.

_SCHEMA_V5 = """
CREATE TABLE memory_claims (
    id             TEXT PRIMARY KEY,
    memory_id      TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    ref            TEXT NOT NULL,
    state          TEXT NOT NULL,
    evidence       TEXT,
    resolved_state TEXT,
    resolved_by    TEXT REFERENCES memories(id) ON DELETE SET NULL,
    resolved_at    TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (memory_id, kind, ref)
);

CREATE INDEX idx_claims_ref ON memory_claims(kind, ref);
CREATE INDEX idx_claims_memory ON memory_claims(memory_id);
CREATE INDEX idx_claims_open ON memory_claims(kind, ref, state)
    WHERE resolved_at IS NULL;
"""


def _migration_005_claims(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V5)
    _backfill_claims(conn)


def _backfill_claims(conn: sqlite3.Connection) -> int:
    """Derive claims for memories that already exist. Returns rows written.

    Claims are only written on store/update, so without this the table would
    stay empty for every existing user and the feature would do nothing until
    they happened to edit a memory. Migration 004 (embeddings) has the same
    shape and solves it with a *startup* backfill instead — correctly, because
    encoding needs an ~80MB model download and must stay lazy and batched.

    Claim extraction is pure regex over text already in the row (measured:
    ~210ms for 735 memories, no I/O), so it belongs in the migration, where
    ``PRAGMA user_version`` guarantees it runs exactly once. A startup pass
    would be wrong here for a subtler reason: most memories legitimately have
    *zero* claims, so "has no claim rows" cannot distinguish never-processed
    from processed-and-empty, and every boot would rescan the whole corpus.

    Positional row access throughout — callers may hand us a connection with
    no ``row_factory`` set.
    """
    from .. import claims as claims_mod  # stdlib-only module; no import cycle

    rows = conn.execute(
        "SELECT m.id, m.title, m.content, n.name FROM memories m "
        "JOIN namespaces n ON n.id = m.namespace_id "
        "WHERE m.confidence != 'deprecated'"
    ).fetchall()
    now = datetime.now(UTC).isoformat()
    # Namespace NAMES only. This runs as a migration backfill, and at this
    # point in the chain ``namespaces.default_repo`` may not exist yet - it
    # arrives in a later migration. Reading it here fails the whole upgrade.
    repos = frozenset(
        row[0] for row in conn.execute("SELECT name FROM namespaces").fetchall() if row[0]
    )
    written = 0
    for memory_id, title, content, namespace in rows:
        for claim in claims_mod.extract_claims(
            title or "", content or "", namespace_default=namespace, known_repos=repos
        ):
            conn.execute(
                "INSERT OR IGNORE INTO memory_claims "
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
    if written:
        logger.info("Backfilled %d state claims across %d memories", written, len(rows))
    return written


# --- Migration 006: repair DBs stamped v5 before 005 learned to backfill -----


def _migration_006_repair_claims_backfill(conn: sqlite3.Connection) -> None:
    """Re-run the claim backfill for DBs that reached v5 without one.

    Migration 005 originally only created the table; the backfill was added a
    few commits later. ``migrate()`` selects pending work with ``current < t``,
    so any DB already stamped 5 can never run 005 again — the fix is
    permanently unreachable there and the table stays empty forever. No
    reinstall or restart helps. Only a *new* version number can reach them.

    Deliberately unconditional rather than guarded on an empty table. A
    stranded DB that has since stored one memory containing a ref is no longer
    empty, and an emptiness guard would skip it for good. ``_backfill_claims``
    is idempotent — ``INSERT OR IGNORE`` against ``UNIQUE (memory_id, kind,
    ref)`` — so on a healthy DB this is a few hundred milliseconds of no-ops,
    once, and existing ``resolved_*`` state is preserved because nothing is
    deleted. Claims are re-derived from each memory's *current* text, so a ref
    edited out of a memory stays gone.
    """
    _backfill_claims(conn)


# --- Migration 007: claim-extraction precision ------------------------------

# Namespaces gingugu itself defines as *not* repos, seeded so bare refs there
# stop keying to a repo that cannot exist. ``default`` is the fallback
# namespace; ``crow`` is the documented global identity namespace. A user who
# genuinely has a repo by either name restores it with
# ``memory_namespaces(action="update", name=..., default_repo=<name>)``.
_NON_REPO_NAMESPACES = ("crow", "default")

_SCHEMA_V7 = """
ALTER TABLE namespaces ADD COLUMN default_repo TEXT;
"""


def _migration_007_claim_precision(conn: sqlite3.Connection) -> None:
    """Add ``namespaces.default_repo`` and re-derive every claim.

    Two extraction defects shipped in v0.10.0, both measured against a real
    785-memory corpus before this was written:

    1. Refs inside ``[[wiki-links]]`` were read as assertions — 11 wrong
       claims, 8 of them in a namespace whose default repo was correct, so
       namespace containment never covered this one.
    2. Every namespace was assumed to be a repo, so bare refs in ``crow``
       keyed to a nonexistent ``crow#N`` — 20 inert but meaningless claims.

    Both fixes only *remove* claims (measured: 156 -> 128, zero gained), so the
    re-derive prunes rather than backfills. It runs through
    ``claim_rederive`` rather than ``claim_sync.sync_claims`` precisely because
    the latter discards resolution state — correct when prose changed, wrong
    here, where the prose is untouched and only the extractor improved.
    Discarding it would destroy manual reconciliation work that cannot be
    recovered.
    """
    conn.executescript(_SCHEMA_V7)
    now = datetime.now(UTC).isoformat()
    for name in _NON_REPO_NAMESPACES:
        conn.execute(
            "UPDATE namespaces SET default_repo = '', updated_at = ? WHERE name = ?",
            (now, name),
        )
    claim_rederive.rederive_claims(conn)


def _migration_009_unverified_claims(conn: sqlite3.Connection) -> None:
    """Re-derive claims now that a state-less ref records as ``unverified``.

    No DDL: ``memory_claims.state`` is plain TEXT with no CHECK constraint, so
    a third state value costs nothing at the schema level. What it does need is
    a re-derive, because claims are stored rows — the smarter extractor changes
    nothing that already exists until something re-reads the prose.

    This is the mirror image of migration 007. That one only ever *removed*
    claims; this one only ever *adds* them, since every ref it newly records is
    one the old extractor dropped on the floor. ``_prune`` is therefore a no-op
    here, and the counts move in one direction only.

    Through ``claim_rederive`` for the same reason as 007: the prose is
    untouched and only the extractor improved, so ``resolved_state`` /
    ``resolved_by`` / ``resolved_at`` must survive. Measured on a 1161-memory
    corpus this writes ~225 new rows and leaves ``claims.open`` unchanged —
    which is the point, and worth asserting after any future change here.
    """
    claim_rederive.rederive_claims(conn)


def _migration_010_claim_qualification(conn: sqlite3.Connection) -> None:
    """Re-derive claims under the corrected repo qualification.

    Four extraction defects were fixed at once, and stored claim rows carry the
    behaviour of whichever extractor wrote them, so fixing the code cleans
    nothing already on disk. This is the third re-derive for that reason; see
    007 and 009.

    Unlike those two, this one moves counts in BOTH directions. It removes refs
    that were never refs - bare "PR 1" naming a position in a planned series,
    and a "NO PR" that bound across a line break to the next list item - and it
    re-keys refs the old extractor attributed to the wrong repo, because a repo
    the prose named outright was discarded whenever it went unrecognized.

    Through ``claim_rederive`` for the same reason as 007 and 009: the prose is
    untouched and only the extractor improved, so ``resolved_state`` /
    ``resolved_by`` / ``resolved_at`` must survive. Measured on a 550-memory
    corpus this removes 66 rows and re-attributes 37, leaving 540 from 569.
    """
    claim_rederive.rederive_claims(conn)
