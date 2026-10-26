"""Shared rig for the Memory Explorer API suites (``gingugu.ui_api``).

A real on-disk brain (the UI reads over a read-only connection, which an
in-memory DB cannot give) seeded with two namespaces, tagged memories, typed
relations, a tripwire, a claim, and fixed-time ``access_log`` / ``query_log``
rows so the events feed has a known timeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gingugu import tripwire
from gingugu.config import load_config
from gingugu.database import Database
from gingugu.models import Confidence, MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore
from tests.test_embeddings import FakeEmbedder

BODY_MARKER = "zq-body-only-5521"

# Fixed timestamps: the events feed is ordered and cursored on these.
T1 = "2026-10-20T10:00:00.000000+00:00"
T2 = "2026-10-20T10:00:05.000000+00:00"
T3 = "2026-10-21T09:30:00.000000+00:00"
T4 = "2026-10-22T08:00:00.000000+00:00"


@dataclass
class Seeded:
    db_path: Path
    ids: dict[str, str] = field(default_factory=dict)
    ns: dict[str, str] = field(default_factory=dict)


def _insert_access(conn, row_id: str, memory_id: str, at: str, context: str) -> None:
    conn.execute(
        "INSERT INTO access_log (id, memory_id, accessed_at, context) VALUES (?, ?, ?, ?)",
        (row_id, memory_id, at, context),
    )


def _insert_query(conn, row_id: str, at: str, tool: str, query: str, ids: list[str]) -> None:
    conn.execute(
        "INSERT INTO query_log (id, session_id, tool, query, namespaces, result_ids, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (row_id, "sess-1", tool, query, json.dumps(["crow"]), json.dumps(ids), at),
    )


def seed(tmp_path: Path, monkeypatch) -> Seeded:
    db_path = tmp_path / "brain.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(db_path))
    # "off", not unset: unset falls through to the developer's machine setting,
    # and a dev Mac in remote mode would send "local" tests to its real brain.
    monkeypatch.setenv("MEMORY_REMOTE_URL", "off")
    database = Database(db_path)
    conn = database.connect()
    nsm = NamespaceManager(conn, load_config())
    store = MemoryStore(conn, embedder=FakeEmbedder())
    crow = nsm.get_or_create("crow")
    proj = nsm.get_or_create("gingugu")
    out = Seeded(db_path=db_path, ns={"crow": crow.id, "gingugu": proj.id})

    def make(key, ns_id, title, tags, *, type_=MemoryType.DECISION, conf=Confidence.VERIFIED):
        mem = store.create(
            namespace_id=ns_id,
            type=type_,
            title=title,
            content=f"{title} body text {BODY_MARKER}",
            confidence=conf,
            tags=tags,
        )
        out.ids[key] = mem.id
        return mem

    make("rule", crow.id, "Never delete a stack parent branch", ["git", "stacking", "rare-x"])
    make("lesson", crow.id, "Retarget child PRs before merging", ["git", "stacking", "rare-x"])
    make("schema", proj.id, "Migrations key off user_version", ["sqlite", "git"])
    make("old", proj.id, "FTS triggers were hand written", ["sqlite"], conf=Confidence.DEPRECATED)
    make("dated", proj.id, "Release notes for the day", ["2026-10-20", "release"])

    rel = RelationManager(conn)
    rel.relate(
        source_id=out.ids["lesson"],
        target_id=out.ids["rule"],
        relation_type=RelationType.CAUSED_BY,
    )
    rel.relate(
        source_id=out.ids["schema"], target_id=out.ids["old"], relation_type=RelationType.SUPERSEDES
    )
    conn.execute("UPDATE memories SET pinned = 1 WHERE id = ?", (out.ids["rule"],))
    tripwire.add_tripwire(conn, out.ids["rule"], "Bash", r"gh pr merge.*--delete-branch")
    conn.execute(
        "INSERT INTO memory_claims (id, memory_id, kind, ref, state, created_at)"
        " VALUES ('c1', ?, 'pr', 'gingugu#124', 'open', ?)",
        (out.ids["schema"], T1),
    )
    _insert_query(conn, "q1", T1, "recall", "stack parent", [out.ids["rule"], out.ids["lesson"]])
    _insert_access(conn, "a1", out.ids["rule"], T2, "recall")
    _insert_query(conn, "q2", T3, "context", "session start", [out.ids["schema"]])
    _insert_access(conn, "a2", out.ids["schema"], T4, "recall")
    conn.commit()
    database.close()
    return out


@pytest.fixture
def seeded(tmp_path, monkeypatch) -> Seeded:
    return seed(tmp_path, monkeypatch)


def snapshot_signals(db_path: Path) -> tuple:
    """Everything a read must never move: access counts, both logs."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        counts = conn.execute(
            "SELECT id, access_count, last_accessed FROM memories ORDER BY id"
        ).fetchall()
        logs = (
            conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0],
        )
    finally:
        conn.close()
    return counts, logs
