"""The language-adapter framework: validation of lang/kind, and package nodes.

A package node (Go imports a directory) carries a blast-radius walk and splits
rank across its files, but is never listed as a dependent.
"""

from __future__ import annotations

import pytest

from gingugu.xray import extract, rank, store, validate
from tests.xray_lang_fixtures import MIXED, _repo

# --- validate -----------------------------------------------------------------------


def _body(files, edges=()):
    return {
        "fingerprint": "a" * 64,
        "extractor_version": 2,
        "commit": None,
        "files": files,
        "edges": list(edges),
        "unresolved": 0,
        "dynamic": 0,
        "external": 0,
        "parse_errors": [],
    }


def test_a_file_without_lang_or_kind_is_a_python_file():
    v = validate.payload(_body([{"path": "a.py", "is_test": False, "is_entry": False}]))
    assert v["files"]["a.py"] == (False, False, "python", "file")


@pytest.mark.parametrize(
    "extra",
    [
        {"lang": "Python"},
        {"lang": ""},
        {"lang": 3},
        {"lang": "x" * 40},
        {"kind": "module"},
        {"kind": "package", "is_test": True},
        {"kind": "package", "is_entry": True},
    ],
)
def test_bad_lang_or_kind_is_refused(extra):
    row = {"path": "a", "is_test": False, "is_entry": False, **extra}
    with pytest.raises(validate.XrayStoreError):
        validate.payload(_body([row]))


def test_package_and_member_edges_are_valid_kinds():
    files = [
        {"path": "a.go", "is_test": False, "is_entry": False, "lang": "go"},
        {"path": "m/p", "is_test": False, "is_entry": False, "lang": "go", "kind": "package"},
    ]
    edges = [
        {"src": "a.go", "dst": "m/p", "kind": "package", "certain": True, "implicit": False},
        {"src": "m/p", "dst": "a.go", "kind": "member", "certain": True, "implicit": False},
    ]
    assert len(validate.payload(_body(files, edges))["edges"]) == 2


# --- rank + store: packages carry the walk, never the listing ---------------------


def test_blast_walks_through_a_hidden_package():
    files = {"lib/a": False, "pkg": False, "app": False, "t": True}
    links = [
        rank.Link("pkg", "lib/a"),
        rank.Link("app", "pkg"),
        rank.Link("t", "pkg"),
    ]
    out = rank.blast(["lib/a"], files, links, rank.pagerank(sorted(files), links), hidden={"pkg"})
    assert [r["path"] for r in out["code"]] == ["app"]
    assert out["code"][0]["hops"] == 2
    assert [r["path"] for r in out["tests"]] == ["t"]


def test_a_package_splits_its_rank_across_its_files():
    # One importer of a 1-file package and a 3-file package: without package
    # nodes the 3-file package's files would take 3/4 of the importer's rank.
    nodes = ["app", "small", "big", "s1", "b1", "b2", "b3"]
    links = [
        rank.Link("app", "small"),
        rank.Link("app", "big"),
        rank.Link("small", "s1"),
        *(rank.Link("big", b) for b in ("b1", "b2", "b3")),
    ]
    r = rank.pagerank(nodes, links)
    assert r["s1"] > r["b1"]
    assert r["b1"] == pytest.approx(r["b2"]) == pytest.approx(r["b3"])


@pytest.fixture
def conn():
    import sqlite3

    from gingugu.migrations import migrate

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    migrate(c)
    yield c
    c.close()


def _ingest(conn, tmp_path):
    import uuid

    from gingugu.models import utcnow_iso

    now, ns = utcnow_iso(), str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?, 'p', ?, ?)",
        (ns, now, now),
    )
    x = extract.extract(_repo(tmp_path, MIXED))
    return store.ingest(conn, ns, x.payload())["snapshot_id"]


def test_store_blast_never_lists_a_package(conn, tmp_path, fake):
    sid = _ingest(conn, tmp_path)
    out = store.blast(conn, sid, ["lib/a.fk"])
    assert [r["path"] for r in out["code"]] == ["app/main.fk"]
    assert [r["path"] for r in out["tests"]] == ["lib/lib_test.fk"]


def test_overview_names_languages_and_marks_packages(conn, tmp_path, fake):
    out = store.overview(conn, _ingest(conn, tmp_path), top=50)
    assert out["languages"] == {"fake": 4, "python": 2}
    rows = {r["path"]: r for r in out["top"]}
    assert rows["fake:lib"]["kind"] == "package"
    assert rows["lib/a.fk"]["lang"] == "fake"
    # Membership is not an import: lib/a.fk is reached only through its package.
    assert rows["lib/a.fk"]["imported_by"] == 0
    assert rows["fake:lib"]["imported_by"] == 2
    assert all(not p.startswith("fake:") for p in out["unreachable"])
