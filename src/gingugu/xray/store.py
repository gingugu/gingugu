"""X-ray snapshots on the brain: ingest what a client extracted, answer from it.

Nothing here reads a file. A payload is data - paths are names inside a
snapshot, never opened - so it is validated as data: every path relative and
plain, every edge between files the payload lists, every count a count. A bad
payload is refused whole, before anything is written.

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
import re
import sqlite3
import uuid

from .. import grants
from ..models import utcnow_iso
from . import rank

KEEP = 5
KINDS = ("module", "conditional", "lazy", "type_checking")
MAX_FILES = 200_000
MAX_EDGES = 2_000_000
_MAX_PATH = 1024
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_COUNTS = ("unresolved", "dynamic", "external")


class XrayStoreError(ValueError):
    pass


def _path(value) -> str:
    ok = (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_PATH
        and not value.startswith("/")
        and "\\" not in value
        and "\x00" not in value
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )
    if not ok:
        raise XrayStoreError(f"invalid path {value!r}: must be relative, '/'-separated, no '..'")
    return value


def _paths(payload: dict, key: str, known: set[str] | None = None) -> list[str]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise XrayStoreError(f"{key} must be a list")
    out = [_path(p) for p in value]
    if known is not None and any(p not in known for p in out):
        raise XrayStoreError(f"{key} names a file the payload does not list")
    return sorted(set(out))


def _validate(payload) -> dict:
    if not isinstance(payload, dict):
        raise XrayStoreError("payload must be an object")
    fp = payload.get("fingerprint")
    if not isinstance(fp, str) or not _FINGERPRINT.fullmatch(fp):
        raise XrayStoreError("fingerprint must be 64 lowercase hex characters")
    version = payload.get("extractor_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise XrayStoreError("extractor_version must be a positive integer")
    commit = payload.get("commit")
    if commit is not None and not (
        isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{4,64}", commit)
    ):
        raise XrayStoreError("commit must be a hex sha or null")
    counts = {}
    for key in _COUNTS:
        n = payload.get(key)
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise XrayStoreError(f"{key} must be a non-negative integer")
        counts[key] = n

    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or len(raw_files) > MAX_FILES:
        raise XrayStoreError(f"files must be a list of at most {MAX_FILES}")
    files: dict[str, tuple[bool, bool]] = {}
    for f in raw_files:
        if not isinstance(f, dict) or not all(
            isinstance(f.get(k), bool) for k in ("is_test", "is_entry")
        ):
            raise XrayStoreError("each file needs path, is_test and is_entry")
        files[_path(f.get("path"))] = (f["is_test"], f["is_entry"])

    raw_edges = payload.get("edges")
    if not isinstance(raw_edges, list) or len(raw_edges) > MAX_EDGES:
        raise XrayStoreError(f"edges must be a list of at most {MAX_EDGES}")
    edges: dict[tuple[str, str], tuple[str, bool]] = {}
    for e in raw_edges:
        if not isinstance(e, dict):
            raise XrayStoreError("each edge must be an object")
        src, dst = _path(e.get("src")), _path(e.get("dst"))
        if src not in files or dst not in files:
            raise XrayStoreError(f"edge {src} -> {dst} names a file the payload does not list")
        if e.get("kind") not in KINDS or not isinstance(e.get("certain"), bool):
            raise XrayStoreError(
                f"edge {src} -> {dst}: kind must be one of {KINDS}, certain a bool"
            )
        if src != dst:
            edges[(src, dst)] = (e["kind"], e["certain"])

    return {
        "fingerprint": fp,
        "extractor_version": version,
        "commit": commit,
        "files": files,
        "edges": edges,
        "parse_errors": _paths(payload, "parse_errors", set(files)),
        "skipped_symlinks": _paths(payload, "skipped_symlinks"),
        **counts,
    }


def _meta(row: sqlite3.Row) -> dict:
    parse_errors = json.loads(row["parse_errors"])
    return {
        "snapshot_id": row["id"],
        "fingerprint": row["fingerprint"],
        "extractor_version": row["extractor_version"],
        "commit": row["commit_sha"],
        "files": row["file_count"],
        "edges": row["edge_count"],
        "created_at": row["created_at"],
        "coverage": {
            "complete": not parse_errors,
            "parse_errors": parse_errors,
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


def ingest(conn: sqlite3.Connection, namespace_id: str, payload) -> dict:
    """Store a snapshot, or touch the identical one already stored. Atomic."""
    grants.require_write_id(namespace_id, "this namespace's X-ray")
    v = _validate(payload)
    found = _row(conn, namespace_id, v["fingerprint"])
    if found is not None:
        _touch(conn, found["id"])
        return {"snapshot_id": found["id"], "fingerprint": v["fingerprint"], "created": False}

    links = [rank.Link(s, d, c) for (s, d), (_, c) in v["edges"].items()]
    ranks = rank.pagerank(sorted(v["files"]), links)
    sid, now = str(uuid.uuid4()), utcnow_iso()
    with conn:  # one transaction: a snapshot is all there or not at all
        conn.execute(
            "INSERT INTO xray_snapshots(id, namespace_id, fingerprint, extractor_version, "
            "commit_sha, file_count, edge_count, unresolved, dynamic, external, parse_errors, "
            "skipped_symlinks, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid,
                namespace_id,
                v["fingerprint"],
                v["extractor_version"],
                v["commit"],
                len(v["files"]),
                len(v["edges"]),
                v["unresolved"],
                v["dynamic"],
                v["external"],
                json.dumps(v["parse_errors"]),
                json.dumps(v["skipped_symlinks"]),
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
            "INSERT INTO xray_edges(snapshot_id, src, dst, kind, certain) VALUES (?, ?, ?, ?, ?)",
            [(sid, s, d, k, int(c)) for (s, d), (k, c) in v["edges"].items()],
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
        rank.Link(r["src"], r["dst"], bool(r["certain"]))
        for r in conn.execute(
            "SELECT src, dst, certain FROM xray_edges WHERE snapshot_id = ?", (snapshot_id,)
        )
    ]
    return files, entries, ranks, links


def blast(conn, snapshot_id: str, seeds: list[str], *, limit=None, offset: int = 0) -> dict:
    """What a change to ``seeds`` reaches, by import, in one snapshot."""
    row = _snapshot(conn, snapshot_id)
    files, _, ranks, links = _graph(conn, snapshot_id)
    out = rank.blast(seeds, files, links, ranks, limit=limit, offset=offset)
    return {**out, "snapshot": _meta(row)}


def overview(conn, snapshot_id: str, *, top: int = 20) -> dict:
    """The most load-bearing files, and the files no root reaches."""
    row = _snapshot(conn, snapshot_id)
    files, entries, ranks, links = _graph(conn, snapshot_id)
    imported_by: dict[str, int] = {}
    for ln in links:
        imported_by[ln.dst] = imported_by.get(ln.dst, 0) + 1
    n = len(files) or 1
    ordered = sorted(ranks, key=lambda p: (-ranks[p], p))[: max(0, top)]
    tests = {p for p, is_test in files.items() if is_test}
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
        "unreachable": rank.unreachable(files, links, roots=tests | entries),
        "roots": {"tests": len(tests), "entries": len(entries)},
    }
