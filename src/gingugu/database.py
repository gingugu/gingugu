"""SQLite connection management, schema migrations, and FTS5 setup.

Migrations are hand-rolled and keyed off ``PRAGMA user_version`` (Alembic is
overkill for a single-file DB). WAL mode and foreign keys are enabled on every
connection. The FTS5 external-content index is kept in sync via triggers —
verified working against SQLite 3.50 (see spike #2).

CAVEAT: ``memories`` has a TEXT primary key, so FTS5 keys off the *implicit*
rowid. ``VACUUM`` may renumber implicit rowids and silently desync the index.
Nothing in this codebase runs VACUUM; if you ever do so manually, rebuild the
index afterwards: ``INSERT INTO memories_fts(memories_fts) VALUES('rebuild')``.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import claim_rederive

logger = logging.getLogger(__name__)

# --- Migration 001: initial schema -----------------------------------------

_SCHEMA_V1 = """
CREATE TABLE namespaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    path        TEXT,
    description TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE memories (
    id              TEXT PRIMARY KEY,
    namespace_id    TEXT NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
    type            TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    confidence      TEXT NOT NULL DEFAULT 'inferred',
    source          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_accessed   TEXT NOT NULL,
    last_confirmed  TEXT,
    access_count    INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT
);

CREATE INDEX idx_memories_namespace ON memories(namespace_id);
CREATE INDEX idx_memories_type ON memories(type);
CREATE INDEX idx_memories_last_accessed ON memories(last_accessed);

CREATE VIRTUAL TABLE memories_fts USING fts5(
    title,
    content,
    content=memories,
    content_rowid=rowid,
    tokenize='porter unicode61'
);

CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, content)
    VALUES (new.rowid, new.title, new.content);
END;

CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content)
    VALUES ('delete', old.rowid, old.title, old.content);
END;

CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content)
    VALUES ('delete', old.rowid, old.title, old.content);
    INSERT INTO memories_fts(rowid, title, content)
    VALUES (new.rowid, new.title, new.content);
END;

CREATE TABLE access_log (
    id          TEXT PRIMARY KEY,
    memory_id   TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    accessed_at TEXT NOT NULL,
    context     TEXT
);

CREATE INDEX idx_access_log_memory_time ON access_log(memory_id, accessed_at);
"""


def _migration_001_initial_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V1)


# --- Migration 002: credential vault ---------------------------------------

_SCHEMA_V2 = """
CREATE TABLE credential_services (
    id           TEXT PRIMARY KEY,
    service_name TEXT NOT NULL UNIQUE,
    description  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    expires_at   TEXT
);

CREATE TABLE credential_fields (
    id           TEXT PRIMARY KEY,
    service_id   TEXT NOT NULL REFERENCES credential_services(id) ON DELETE CASCADE,
    field_name   TEXT NOT NULL,
    is_secret    INTEGER NOT NULL DEFAULT 1,
    plain_value  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE(service_id, field_name)
);

CREATE INDEX idx_credential_fields_service ON credential_fields(service_id);
"""


def _migration_002_credential_vault(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V2)


# --- Migration 003: tags + relations (knowledge graph) ---------------------

_SCHEMA_V3 = """
CREATE TABLE tags (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE memory_tags (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    tag_id    TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (memory_id, tag_id)
);

CREATE INDEX idx_memory_tags_tag ON memory_tags(tag_id);

CREATE TABLE relations (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    target_id     TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    metadata      TEXT,
    UNIQUE(source_id, target_id, relation_type)
);

CREATE INDEX idx_relations_source ON relations(source_id);
CREATE INDEX idx_relations_target ON relations(target_id);
"""


def _migration_003_tags_relations(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V3)


# --- Migration 004: semantic embeddings ------------------------------------
#
# One embedding row per memory. The vector is stored as a packed float32
# BLOB (see embeddings.pack/unpack). Embedding rows are optional — a
# memory without one simply falls back to BM25-only ranking during search.
# Storing the model name + dim alongside the blob lets us safely re-encode
# if the active model changes (mismatched dims won't be combined silently).

_SCHEMA_V4 = """
CREATE TABLE memory_embeddings (
    memory_id  TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    embedding  BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _migration_004_embeddings(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V4)


# --- Migration 005: extracted state claims ----------------------------------
#
# A memory that says "PR #10 is open" is making a CLAIM. That claim was true
# when written and goes silently wrong the moment the PR merges — but the
# prose is honest history and must never be edited to track it. So the claim
# lives here as data instead, keyed to something checkable.
#
# ``ref`` is repo-qualified ("gingugu#10"), because "PR #12" is not a global
# key — gingugu#12 and VersatermTechPlatform#12 are different objects. A ref
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
    from . import claims as claims_mod  # stdlib-only module; no import cycle

    rows = conn.execute(
        "SELECT m.id, m.title, m.content, n.name FROM memories m "
        "JOIN namespaces n ON n.id = m.namespace_id "
        "WHERE m.confidence != 'deprecated'"
    ).fetchall()
    now = datetime.now(UTC).isoformat()
    written = 0
    for memory_id, title, content, namespace in rows:
        for claim in claims_mod.extract_claims(
            title or "", content or "", namespace_default=namespace
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


# (target_version, migration_callable) — applied in order when current < target.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_initial_schema),
    (2, _migration_002_credential_vault),
    (3, _migration_003_tags_relations),
    (4, _migration_004_embeddings),
    (5, _migration_005_claims),
    (6, _migration_006_repair_claims_backfill),
    (7, _migration_007_claim_precision),
]


def _backup_before_migration(
    db_path: Path, current_version: int, target_version: int
) -> Path | None:
    """Copy the DB to ``<name>.bak-before-vN`` before applying migrations.

    Skipped for in-memory DBs and for first-time DB creation
    (``current_version == 0``). If a backup file for this target version
    already exists (e.g. from a previous failed migration attempt) we leave
    it alone — overwriting could destroy the only known-good copy.

    Returns the backup path on success, ``None`` if skipped.
    """
    if str(db_path) == ":memory:":
        return None
    if current_version == 0:  # fresh DB — nothing worth backing up yet
        return None
    if not db_path.exists():
        return None

    backup_path = db_path.with_name(f"{db_path.name}.bak-before-v{target_version}")
    if backup_path.exists():
        logger.info(
            "Pre-migration backup already exists at %s; leaving it intact",
            backup_path,
        )
        return backup_path

    shutil.copy2(db_path, backup_path)
    logger.info(
        "Pre-migration backup created: %s (v%d -> v%d)",
        backup_path,
        current_version,
        target_version,
    )
    return backup_path


def migrate(conn: sqlite3.Connection, db_path: Path | None = None) -> int:
    """Apply pending migrations. Returns the resulting schema version.

    When ``db_path`` is provided and migrations are pending, a one-shot
    backup of the live DB file is taken before the first migration runs
    (``<db>.bak-before-vN`` where N is the first pending target). The
    backup is best-effort: if it fails the migration still proceeds.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [(t, fn) for t, fn in MIGRATIONS if current < t]
    if pending and db_path is not None:
        try:
            _backup_before_migration(db_path, current, pending[0][0])
        except OSError as e:  # disk full, permissions, etc.
            logger.warning("Pre-migration backup failed (continuing): %s", e)
    for target, fn in pending:
        logger.info("Applying migration -> v%d", target)
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
        conn.commit()
        current = target
    return current


class Database:
    """Owns a single SQLite connection with WAL + foreign keys enabled."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        migrate(conn, db_path=self.db_path)
        self._conn = conn
        logger.info("Database ready at %s", self.db_path)
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self.connect()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
