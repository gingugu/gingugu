"""X-ray snapshots on the brain: ingest what a client extracted, answer from it.

Nothing here reads a file. A payload is data, validated as data before any of
it is written (``validate``).

The fence is the namespace's, through the same grant chokepoints as memories:
write to ingest, read to query. A snapshot the caller cannot read is reported
as unknown, the way a hidden memory reads as missing.

**Retention keeps the most recently USED, not created.** Two worktrees or two
machines on one namespace each have their own snapshot, and the one an agent is
querying right now must not be pruned because someone else refreshed five
times. Old graphs are cheap to rebuild from git, so ``KEEP`` is small.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from .. import grants
from ..models import utcnow_iso
from . import rank, validate
from .validate import DELIBERATE, XrayStoreError

__all__ = ["KEEP", "XrayStoreError", "blast", "find", "ingest", "overview"]

KEEP = 5


def _meta(row: sqlite3.Row) -> dict:
    parse_errors, skipped = json.loads(row["parse_errors"]), json.loads(row["skipped"])
    missing = [s for s in skipped if s["reason"] not in DELIBERATE]
    return {
        "snapshot_id": row["id"],
        "fingerprint": row["fingerprint"],
        "extractor_version": row["extractor_version"],
        "commit": row["commit_sha"],
        "files": row["file_count"],
        "edges": row["edge_count"],
        "created_at": row["created_at"],
        "coverage": {
            # Complete = every Python source the client meant to read was read and
            # parsed. A deliberate exclusion (a symlink) does not count against it.
            "complete": not parse_errors and not missing,
            "parse_errors": parse_errors,
            "skipped": skipped,
            "unresolved": row["unresolved"],
            "dynamic": row["dynamic"],
            "external": row["external"],
        },
    }


def _row(conn, namespace_id: str, fingerprint: str | None):
    if fingerprint is None:
        sql, args = "ORDER BY last_used_at DESC, created_at DESC LIMIT 1", (namespace_id,)
        return conn.execute(
            f"SELECT * FROM xray_snapshots WHERE namespace_id = ? {sql}", args
        ).fetchone()
    return conn.execute(
        "SELECT * FROM xray_snapshots WHERE namespace_id = ? AND fingerprint = ?",
        (namespace_id, fingerprint),
    ).fetchone()


def _touch(conn, snapshot_id: str) -> None:
    conn.execute(
        "UPDATE xray_snapshots SET last_used_at = ? WHERE id = ?", (utcnow_iso(), snapshot_id)
    )


def _prune(conn, namespace_id: str) -> None:
    conn.execute(
        "DELETE FROM xray_snapshots WHERE namespace_id = ? AND id NOT IN ("
        " SELECT id FROM xray_snapshots WHERE namespace_id = ?"
        " ORDER BY last_used_at DESC, created_at DESC LIMIT ?)",
        (namespace_id, namespace_id, KEEP),
    )


def ingest(conn: sqlite3.Connection, namespace_id: str, payload, *, replace: bool = False) -> dict:
    """Store a snapshot, or touch the one already stored for that fingerprint. Atomic.

    ``replace`` (an explicit refresh) swaps a stored snapshot whose graph differs
    from this one: the fingerprint is the client's claim, the graph digest is not.
    """
    grants.require_write_id(namespace_id, "this namespace's X-ray")
    v = validate.payload(payload)
    found = _row(conn, namespace_id, v["fingerprint"])
    if found is not None and not (replace and found["graph_digest"] != v["graph_digest"]):
        _touch(conn, found["id"])
        return {"snapshot_id": found["id"], "fingerprint": v["fingerprint"], "created": False}

    links = [rank.Link(s, d, c, i) for (s, d), (_, c, i) in v["edges"].items()]
    ranks = rank.pagerank(sorted(v["files"]), links)
    sid, now = str(uuid.uuid4()), utcnow_iso()
    with conn:  # one transaction: a snapshot is all there or not at all
        if found is not None:
            conn.execute("DELETE FROM xray_snapshots WHERE id = ?", (found["id"],))
        conn.execute(
            "INSERT INTO xray_snapshots(id, namespace_id, fingerprint, graph_digest, "
            "extractor_version, commit_sha, file_count, edge_count, unresolved, dynamic, "
            "external, parse_errors, skipped, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid,
                namespace_id,
                v["fingerprint"],
                v["graph_digest"],
                v["extractor_version"],
                v["commit"],
                len(v["files"]),
                len(v["edges"]),
                v["unresolved"],
                v["dynamic"],
                v["external"],
                json.dumps(v["parse_errors"]),
                json.dumps(v["skipped"]),
                now,
                now,
            ),
        )
        conn.executemany(
            "INSERT INTO xray_files(snapshot_id, path, is_test, is_entry, rank) "
            "VALUES (?, ?, ?, ?, ?)",
            [(sid, p, int(t), int(e), ranks[p]) for p, (t, e) in v["files"].items()],
        )
        conn.executemany(
            "INSERT INTO xray_edges(snapshot_id, src, dst, kind, certain, implicit) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(sid, s, d, k, int(c), int(i)) for (s, d), (k, c, i) in v["edges"].items()],
        )
        _prune(conn, namespace_id)
    return {"snapshot_id": sid, "fingerprint": v["fingerprint"], "created": True}


def find(
    conn: sqlite3.Connection, namespace_id: str, fingerprint: str | None = None
) -> dict | None:
    """A snapshot's metadata by fingerprint, or the namespace's most recently used."""
    if not grants.can_read_id(namespace_id):
        return None
    row = _row(conn, namespace_id, fingerprint)
    if row is None:
        return None
    _touch(conn, row["id"])
    return _meta(row)


def _snapshot(conn, snapshot_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM xray_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if row is None or not grants.can_read_id(row["namespace_id"]):
        raise XrayStoreError(f"unknown snapshot {snapshot_id!r}")
    return row


def _graph(conn, snapshot_id: str):
    rows = conn.execute(
        "SELECT path, is_test, is_entry, rank FROM xray_files WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchall()
    files = {r["path"]: bool(r["is_test"]) for r in rows}
    entries = {r["path"] for r in rows if r["is_entry"]}
    ranks = {r["path"]: r["rank"] for r in rows}
    links = [
        rank.Link(r["src"], r["dst"], bool(r["certain"]), bool(r["implicit"]))
        for r in conn.execute(
            "SELECT src, dst, certain, implicit FROM xray_edges WHERE snapshot_id = ?",
            (snapshot_id,),
        )
    ]
    return files, entries, ranks, links


def blast(conn, snapshot_id: str, seeds: list[str], *, limit=None, offset: int = 0) -> dict:
    """What a change to ``seeds`` reaches, by import, in one snapshot."""
    row = _snapshot(conn, snapshot_id)
    files, _, ranks, links = _graph(conn, snapshot_id)
    out = rank.blast(seeds, files, links, ranks, limit=limit, offset=offset)
    return {**out, "snapshot": _meta(row)}


def overview(conn, snapshot_id: str, *, top: int = 20, limit=None, offset: int = 0) -> dict:
    """The most load-bearing files, and the files no root reaches (paged)."""
    row = _snapshot(conn, snapshot_id)
    files, entries, ranks, links = _graph(conn, snapshot_id)
    imported_by: dict[str, int] = {}
    for ln in links:
        if not ln.implicit:  # counted like rank: who asked for it, not who ran it
            imported_by[ln.dst] = imported_by.get(ln.dst, 0) + 1
    n = len(files) or 1
    ordered = sorted(ranks, key=lambda p: (-ranks[p], p))[: max(0, top)]
    tests = {p for p, is_test in files.items() if is_test}
    dead = rank.unreachable(files, links, roots=tests | entries)
    end = None if limit is None else offset + limit
    return {
        "snapshot": _meta(row),
        "top": [
            {
                "path": p,
                "rank": round(ranks[p], 8),
                "times_baseline": round(ranks[p] * n, 2),
                "imported_by": imported_by.get(p, 0),
            }
            for p in ordered
        ],
        "unreachable": None if dead is None else dead[offset:end],
        "unreachable_total": None if dead is None else len(dead),
        "roots": {"tests": len(tests), "entries": len(entries)},
    }
