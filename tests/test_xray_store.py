"""X-ray snapshots on the brain: ingest, find, blast, overview, retention, fence.

The brain never reads a path. It stores what the client extracted, keyed by
namespace + content fingerprint, ranks it once at ingest, and answers from it.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from gingugu import grants
from gingugu.grants import READ, WRITE, Grant
from gingugu.migrations import migrate
from gingugu.models import utcnow_iso
from gingugu.xray import store
from gingugu.xray.store import XrayStoreError


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    migrate(c)
    yield c
    c.close()


def _ns(conn, name: str) -> str:
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (ns_id, name, now, now),
    )
    return ns_id


def _payload(fp: str = "a" * 64, **over) -> dict:
    body = {
        "fingerprint": fp,
        "extractor_version": 1,
        "commit": "c" * 40,
        "files": [
            {"path": "core.py", "is_test": False, "is_entry": False},
            {"path": "svc.py", "is_test": False, "is_entry": False},
            {"path": "cli.py", "is_test": False, "is_entry": True},
            {"path": "orphan.py", "is_test": False, "is_entry": False},
            {"path": "tests/test_svc.py", "is_test": True, "is_entry": False},
        ],
        "edges": [
            {
                "src": "svc.py",
                "dst": "core.py",
                "kind": "module",
                "certain": True,
                "implicit": False,
            },
            {"src": "cli.py", "dst": "svc.py", "kind": "lazy", "certain": True, "implicit": False},
            {
                "src": "tests/test_svc.py",
                "dst": "svc.py",
                "kind": "module",
                "certain": False,
                "implicit": False,
            },
        ],
        "unresolved": 1,
        "dynamic": 0,
        "external": 7,
        "parse_errors": [],
    }
    body.update(over)
    return body


# --- ingest ---------------------------------------------------------------------


def test_ingest_stores_a_snapshot_and_is_idempotent(conn):
    ns = _ns(conn, "repo")
    first = store.ingest(conn, ns, _payload())
    assert first["created"] is True
    again = store.ingest(conn, ns, _payload())
    assert again["created"] is False
    assert again["snapshot_id"] == first["snapshot_id"]
    n = conn.execute("SELECT COUNT(*) FROM xray_snapshots").fetchone()[0]
    assert n == 1


def test_the_same_fingerprint_in_two_namespaces_is_two_snapshots(conn):
    a, b = _ns(conn, "a"), _ns(conn, "b")
    assert (
        store.ingest(conn, a, _payload())["snapshot_id"]
        != store.ingest(conn, b, _payload())["snapshot_id"]
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"fingerprint": "nothex"},
        {"extractor_version": "1"},
        {"files": [{"path": "/etc/passwd", "is_test": False, "is_entry": False}]},
        {"files": [{"path": "../up.py", "is_test": False, "is_entry": False}]},
        {"files": [{"path": "a\\b.py", "is_test": False, "is_entry": False}]},
        {
            "edges": [
                {
                    "src": "core.py",
                    "dst": "ghost.py",
                    "kind": "module",
                    "certain": True,
                    "implicit": False,
                }
            ]
        },
        {
            "edges": [
                {
                    "src": "core.py",
                    "dst": "svc.py",
                    "kind": "weird",
                    "certain": True,
                    "implicit": False,
                }
            ]
        },
        {
            "edges": [
                {
                    "src": "core.py",
                    "dst": "svc.py",
                    "kind": "module",
                    "certain": "yes",
                    "implicit": False,
                }
            ]
        },
        {"unresolved": -1},
        {"parse_errors": ["ghost.py"]},
    ],
)
def test_ingest_validates_before_writing(conn, bad):
    ns = _ns(conn, "repo")
    with pytest.raises(XrayStoreError):
        store.ingest(conn, ns, _payload(**bad))
    assert conn.execute("SELECT COUNT(*) FROM xray_snapshots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM xray_files").fetchone()[0] == 0


def test_ingest_requires_write_on_the_namespace(conn):
    ns = _ns(conn, "repo")
    with grants.bind(Grant("ro", {"repo": READ}), conn), pytest.raises(grants.AccessDenied):
        store.ingest(conn, ns, _payload())


def test_retention_keeps_the_most_recently_used(conn, monkeypatch):
    ns = _ns(conn, "repo")
    clock = iter(f"2026-10-26T00:00:{i:02d}+00:00" for i in range(60))
    monkeypatch.setattr(store, "utcnow_iso", lambda: next(clock))
    ids = [store.ingest(conn, ns, _payload(f"{i:x}" * 64))["snapshot_id"] for i in range(5)]
    store.find(conn, ns, "0" * 64)  # the oldest is in use: it must survive
    store.ingest(conn, ns, _payload("f" * 64))
    kept = {r[0] for r in conn.execute("SELECT id FROM xray_snapshots")}
    assert len(kept) == store.KEEP == 5
    assert ids[0] in kept
    assert ids[1] not in kept
    assert (
        conn.execute("SELECT COUNT(*) FROM xray_files WHERE snapshot_id = ?", (ids[1],)).fetchone()[
            0
        ]
        == 0
    )


def test_a_namespace_delete_takes_its_snapshots(conn):
    ns = _ns(conn, "repo")
    store.ingest(conn, ns, _payload())
    conn.execute("DELETE FROM namespaces WHERE id = ?", (ns,))
    assert conn.execute("SELECT COUNT(*) FROM xray_edges").fetchone()[0] == 0


# --- find -----------------------------------------------------------------------


def test_find_by_fingerprint_or_latest(conn):
    ns = _ns(conn, "repo")
    assert store.find(conn, ns, "a" * 64) is None
    assert store.find(conn, ns) is None
    store.ingest(conn, ns, _payload("a" * 64))
    store.ingest(conn, ns, _payload("b" * 64))
    assert store.find(conn, ns)["fingerprint"] == "b" * 64
    assert store.find(conn, ns, "a" * 64)["fingerprint"] == "a" * 64
    # Finding counts as using: the latest is now the one just read.
    assert store.find(conn, ns)["fingerprint"] == "a" * 64


def test_find_reports_coverage(conn):
    ns = _ns(conn, "repo")
    store.ingest(conn, ns, _payload(parse_errors=["orphan.py"]))
    meta = store.find(conn, ns)
    assert meta["coverage"] == {
        "complete": False,
        "parse_errors": ["orphan.py"],
        "skipped": [],
        "unresolved": 1,
        "dynamic": 0,
        "external": 7,
    }
    assert meta["files"] == 5 and meta["edges"] == 3
    assert meta["commit"] == "c" * 40


def test_find_is_fenced(conn):
    ns = _ns(conn, "repo")
    store.ingest(conn, ns, _payload())
    with grants.bind(Grant("other", {"elsewhere": WRITE}), conn):
        assert store.find(conn, ns) is None


# --- blast / overview -------------------------------------------------------------


def test_blast_answers_from_the_snapshot(conn):
    ns = _ns(conn, "repo")
    sid = store.ingest(conn, ns, _payload())["snapshot_id"]
    out = store.blast(conn, sid, ["core.py"])
    assert [d["path"] for d in out["code"]] == ["svc.py", "cli.py"] or {
        d["path"] for d in out["code"]
    } == {"svc.py", "cli.py"}
    assert {d["path"]: d["hops"] for d in out["code"]} == {"svc.py": 1, "cli.py": 2}
    assert [(d["path"], d["certain"]) for d in out["tests"]] == [("tests/test_svc.py", False)]
    assert out["snapshot"]["snapshot_id"] == sid


def test_blast_on_a_hidden_or_missing_snapshot_is_refused(conn):
    ns = _ns(conn, "repo")
    sid = store.ingest(conn, ns, _payload())["snapshot_id"]
    with pytest.raises(XrayStoreError):
        store.blast(conn, "no-such-id", ["core.py"])
    with grants.bind(Grant("other", {"elsewhere": WRITE}), conn), pytest.raises(XrayStoreError):
        store.blast(conn, sid, ["core.py"])


def test_overview_ranks_and_lists_dead_file_candidates(conn):
    ns = _ns(conn, "repo")
    sid = store.ingest(conn, ns, _payload())["snapshot_id"]
    out = store.overview(conn, sid, top=2)
    assert out["top"][0]["path"] == "core.py"
    assert len(out["top"]) == 2
    assert set(out["top"][0]) >= {"path", "rank", "times_baseline", "imported_by"}
    assert out["top"][0]["imported_by"] == 1
    assert out["unreachable"] == ["orphan.py"]
    assert out["roots"] == {"tests": 1, "entries": 1}


def test_overview_without_roots_says_so(conn):
    ns = _ns(conn, "repo")
    files = [{"path": "a.py", "is_test": False, "is_entry": False}]
    sid = store.ingest(conn, ns, _payload(files=files, edges=[]))["snapshot_id"]
    assert store.overview(conn, sid)["unreachable"] is None


# --- review round: skips, duplicates, caps, replace -------------------------------


def test_skips_are_validated_stored_and_break_completeness_unless_deliberate(conn):
    ns = _ns(conn, "repo")
    store.ingest(
        conn,
        ns,
        _payload(skipped=[{"path": "link.py", "reason": "symlink"}]),
    )
    meta = store.find(conn, ns)
    assert meta["coverage"]["complete"] is True  # a symlink is excluded on purpose
    assert meta["coverage"]["skipped"] == [{"path": "link.py", "reason": "symlink"}]

    store.ingest(
        conn,
        ns,
        _payload("b" * 64, skipped=[{"path": "secret.py", "reason": "unreadable"}]),
    )
    assert store.find(conn, ns, "b" * 64)["coverage"]["complete"] is False


@pytest.mark.parametrize(
    "bad",
    [
        {"skipped": [{"path": "x.py", "reason": "because"}]},
        {"skipped": [{"path": "/abs.py", "reason": "symlink"}]},
        {"skipped": "nope"},
        {"files": [{"path": "a.py", "is_test": False, "is_entry": False}] * 2, "edges": []},
        {
            "edges": [
                {
                    "src": "svc.py",
                    "dst": "core.py",
                    "kind": "module",
                    "certain": True,
                    "implicit": False,
                }
            ]
            * 2
        },
        {"external": 2**40},
    ],
)
def test_review_round_rejections(conn, bad):
    with pytest.raises(XrayStoreError):
        store.ingest(conn, _ns(conn, "repo"), _payload(**bad))


def test_caps_are_enforced(conn, monkeypatch):
    from gingugu.xray import validate

    monkeypatch.setattr(validate, "MAX_FILES", 2)
    with pytest.raises(XrayStoreError):
        store.ingest(conn, _ns(conn, "repo"), _payload())


def test_replace_repairs_a_planted_graph(conn):
    ns = _ns(conn, "repo")
    planted = store.ingest(conn, ns, _payload(edges=[]))
    honest = store.ingest(conn, ns, _payload())
    assert honest["created"] is False  # same fingerprint: kept, not replaced
    repaired = store.ingest(conn, ns, _payload(), replace=True)
    assert repaired["created"] is True
    assert repaired["snapshot_id"] != planted["snapshot_id"]
    assert store.find(conn, ns)["edges"] == 3
    assert conn.execute("SELECT COUNT(*) FROM xray_snapshots").fetchone()[0] == 1


def test_replace_with_identical_content_is_a_touch(conn):
    ns = _ns(conn, "repo")
    first = store.ingest(conn, ns, _payload())
    again = store.ingest(conn, ns, _payload(), replace=True)
    assert again == {**first, "created": False}


def test_overview_pages_unreachable(conn):
    ns = _ns(conn, "repo")
    files = [{"path": f"m{i}.py", "is_test": False, "is_entry": i == 0} for i in range(6)]
    sid = store.ingest(conn, ns, _payload(files=files, edges=[]))["snapshot_id"]
    out = store.overview(conn, sid, limit=2, offset=1)
    assert out["unreachable"] == ["m2.py", "m3.py"]
    assert out["unreachable_total"] == 5
