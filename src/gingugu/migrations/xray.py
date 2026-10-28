"""Migration 019: ``xray_snapshots``, ``xray_files``, ``xray_edges`` - Codebase X-ray.

A repository's import graph, as the client extracted it. Not memories: a
10,000-file repo would be 10,000 nodes in the store's own graph, ranked and
recalled beside the user's knowledge, and nothing in it was put there by a
person. So it gets tables of its own, outside ``memories`` and its FTS triggers.

**Keyed by namespace + content fingerprint, never by path.** A path is the
client's: two Macs check the same repo out in different places, and the brain
must never be handed one to open. The fingerprint covers every Python file's
bytes, ``pyproject.toml`` and the extractor version, so identical content is one
snapshot wherever it came from, and two worktrees with different edits are two.
``UNIQUE(namespace_id, fingerprint)`` is what makes ingest idempotent.

``namespace_id`` cascades as ``memories`` does, and files and edges cascade
from their snapshot, so retention is one ``DELETE`` on ``xray_snapshots``.
An edge's ends are foreign keys into its snapshot's files, so the invariant ingest
validates is also one the schema keeps. ``graph_digest`` hashes the validated
graph: the fingerprint is the client's claim about its checkout and cannot be
checked here, so an explicit refresh replaces a stored snapshot whose graph
differs - the repair path if a writer ever stored a wrong one. ``skipped`` is
JSON ``[{path, reason}]``: sources the client did not read, and why.
Rank is computed once at ingest and stored on ``xray_files``; the graph of a
snapshot never changes, so neither does its rank. ``kind`` is plain TEXT
validated in Python, like the board's, so a new kind needs no table rebuild.
"""

from __future__ import annotations

import sqlite3

_SCHEMA_V19 = """
CREATE TABLE xray_snapshots (
    id                TEXT PRIMARY KEY,
    namespace_id      TEXT NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
    fingerprint       TEXT NOT NULL,
    graph_digest      TEXT NOT NULL,
    extractor_version INTEGER NOT NULL,
    commit_sha        TEXT,
    file_count        INTEGER NOT NULL,
    edge_count        INTEGER NOT NULL,
    unresolved        INTEGER NOT NULL,
    dynamic           INTEGER NOT NULL,
    external          INTEGER NOT NULL,
    parse_errors      TEXT NOT NULL,
    skipped           TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    last_used_at      TEXT NOT NULL,
    UNIQUE (namespace_id, fingerprint)
);

CREATE INDEX idx_xray_snapshots_used ON xray_snapshots(namespace_id, last_used_at);

CREATE TABLE xray_files (
    snapshot_id TEXT NOT NULL REFERENCES xray_snapshots(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    is_test     INTEGER NOT NULL,
    is_entry    INTEGER NOT NULL,
    rank        REAL NOT NULL,
    PRIMARY KEY (snapshot_id, path)
);

CREATE TABLE xray_edges (
    snapshot_id TEXT NOT NULL REFERENCES xray_snapshots(id) ON DELETE CASCADE,
    src         TEXT NOT NULL,
    dst         TEXT NOT NULL,
    kind        TEXT NOT NULL,
    certain     INTEGER NOT NULL,
    implicit    INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, src, dst),
    FOREIGN KEY (snapshot_id, src) REFERENCES xray_files(snapshot_id, path) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id, dst) REFERENCES xray_files(snapshot_id, path) ON DELETE CASCADE
);

CREATE INDEX idx_xray_edges_dst ON xray_edges(snapshot_id, dst);
"""


def _migration_019_xray(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V19)


def _migration_021_xray_languages(conn: sqlite3.Connection) -> None:
    """``xray_files.lang`` and ``kind``: X-ray reads more than Python.

    ``lang`` names the adapter that parsed the node; ``kind`` is ``file`` or
    ``package`` - a synthetic node for languages that import a directory (Go),
    so a package's rank is split across its files rather than multiplied by
    them. Every snapshot stored before this was Python files, so the defaults
    are the truth for old rows and nothing is backfilled. Plain TEXT validated
    in Python, like ``xray_edges.kind``.

    Resumable, like 020: a column already present is skipped.
    """
    have = {row[1] for row in conn.execute("PRAGMA table_info(xray_files)")}
    for column, default in (("lang", "python"), ("kind", "file")):
        if column not in have:
            conn.execute(
                f"ALTER TABLE xray_files ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'"
            )
