"""Structural migrations: the tables and columns the store is built from.

These are the migrations that change the *shape* of the database. Each one
pairs its DDL with the function that applies it, so the reasoning for a table
never drifts away from the table itself. Migrations that derive or re-derive
*rows* live in ``claim_derivation``; the registry that orders them all lives
in this package's ``__init__``.
"""

from __future__ import annotations

import sqlite3

# --- Migration 001: initial schema -----------------------------------------
#
# CAVEAT: ``memories`` has a TEXT primary key, so FTS5 keys off the *implicit*
# rowid. ``VACUUM`` may renumber implicit rowids and silently desync the index.
# Nothing in this codebase runs VACUUM; if you ever do so manually, rebuild the
# index afterwards: ``INSERT INTO memories_fts(memories_fts) VALUES('rebuild')``.
#
# The FTS5 external-content index is kept in sync via the triggers below —
# verified working against SQLite 3.50 (see spike #2).

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


# --- Migration 008: pinned memories -----------------------------------------

_SCHEMA_V8 = """
ALTER TABLE memories ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_memories_pinned ON memories(namespace_id, pinned) WHERE pinned = 1;
"""


def _migration_008_pinned(conn: sqlite3.Connection) -> None:
    """Add the ``pinned`` flag: memories that always load, exempt from ranking.

    Ranking answers "what is most relevant to this task?". It cannot answer
    "what must never be missing?" — those are different questions, and before
    this every governing rule competed for a context slot against topical
    trivia on the same axis. A pin removes a memory from that contest.

    Defaults to 0, so an existing store gains the column and changes no
    behaviour until something is explicitly pinned. The partial index keeps the
    context-load lookup cheap: it only ever indexes the handful of pinned rows,
    not the whole table.

    The FTS5 sync triggers key off ``title``/``content`` only, so a new column
    needs no trigger changes.
    """
    conn.executescript(_SCHEMA_V8)


# --- Migration 011: the dream-pass proposal queue ----------------------------
#
# The queue exists so a background pass can compute structure over the graph
# without ever writing to ``memories``. Everything the pass produces lands
# here, pending, until a person decides on it. That separation is the whole
# design constraint, so it is enforced by the schema rather than by convention:
# nothing in ``dream/`` is given a write path to any other table.
#
# ``evidence`` is JSON holding the numbers that produced the row - the rank, the
# similarity, the member list. A proposal a reader cannot audit is an opinion,
# and an opinion is exactly what this pass is forbidden to have.

_SCHEMA_V11 = """
CREATE TABLE proposals (
    id           TEXT PRIMARY KEY,
    pass_name    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    namespace_id TEXT REFERENCES namespaces(id) ON DELETE CASCADE,
    subject_id   TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    object_id    TEXT REFERENCES memories(id) ON DELETE CASCADE,
    score        REAL NOT NULL,
    evidence     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    decided_at   TEXT
);

CREATE UNIQUE INDEX idx_proposals_identity
    ON proposals(kind, subject_id, COALESCE(object_id, ''));

CREATE INDEX idx_proposals_status ON proposals(status, score DESC);
"""


def _migration_011_proposals(conn: sqlite3.Connection) -> None:
    """Add the proposal queue the dream pass writes to.

    Two details in here are load-bearing rather than incidental.

    **The identity index uses ``COALESCE(object_id, '')``.** SQLite treats NULLs
    as distinct in a unique index, so a plain three-column index would let the
    same single-memory proposal be inserted on every run, once per night,
    forever. Collapsing NULL to the empty string makes re-running the pass an
    update instead of an accumulation.

    **Deciding a proposal keeps the row.** A rejection is not a no-op to be
    cleaned up: it is the record that says "this was computed, and it was
    wrong", which is what stops the next run from proposing it again and what
    the governance work will later count as precedent. Only a cascade from a
    forgotten memory removes anything here.
    """
    conn.executescript(_SCHEMA_V11)
