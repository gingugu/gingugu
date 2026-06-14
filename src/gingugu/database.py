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
import sqlite3
from collections.abc import Callable
from pathlib import Path

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


# (target_version, migration_callable) — applied in order when current < target.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_initial_schema),
    (2, _migration_002_credential_vault),
    (3, _migration_003_tags_relations),
    (4, _migration_004_embeddings),
]


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, fn in MIGRATIONS:
        if current < target:
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
        conn.execute("PRAGMA busy_timeout=5000")
        migrate(conn)
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
