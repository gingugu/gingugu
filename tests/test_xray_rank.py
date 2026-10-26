"""X-ray graph math: directed PageRank, blast radius, unreachable files.

Edges point from importer to imported (``src`` imports ``dst``). Rank flows
the same way: a file the rest of the code leans on ranks high; a file that only
imports others does not.
"""

from __future__ import annotations

import pytest

from gingugu.xray import rank


def _e(*pairs, certain=True):
    return [rank.Link(s, d, certain) for s, d in pairs]


# --- directed PageRank ----------------------------------------------------------


def test_rank_is_a_distribution():
    r = rank.pagerank(["a", "b", "c"], _e(("a", "b"), ("b", "c")))
    assert sum(r.values()) == pytest.approx(1.0)
    assert set(r) == {"a", "b", "c"}


def test_rank_flows_to_the_imported_file():
    r = rank.pagerank(["a", "b", "c"], _e(("a", "b"), ("b", "c")))
    assert r["c"] > r["b"] > r["a"]


def test_a_shared_dependency_outranks_the_file_importing_everything():
    # server imports everything; models is imported by everything. The undirected
    # rank got this backwards (server #2 with in=1, out=26).
    nodes = ["server", "models", "a", "b", "c", "d"]
    edges = _e(
        ("server", "a"),
        ("server", "b"),
        ("server", "c"),
        ("server", "d"),
        ("a", "models"),
        ("b", "models"),
        ("c", "models"),
        ("d", "models"),
    )
    r = rank.pagerank(nodes, edges)
    assert max(r, key=r.get) == "models"
    assert r["server"] == min(r.values())


def test_rank_is_deterministic_and_handles_empty_and_isolated():
    assert rank.pagerank([], []) == {}
    r1 = rank.pagerank(["x", "y"], [])
    assert r1 == rank.pagerank(["x", "y"], [])
    assert r1["x"] == pytest.approx(0.5)


def test_rank_ignores_edges_to_unknown_nodes_and_self_loops():
    r = rank.pagerank(["a", "b"], _e(("a", "b"), ("a", "ghost"), ("b", "b")))
    assert set(r) == {"a", "b"}
    assert sum(r.values()) == pytest.approx(1.0)


def test_implicit_edges_are_ignored_by_rank_but_kept_by_blast():
    files = {"pkg/__init__.py": False, "pkg/mod.py": False, "a.py": False, "b.py": False}
    links = [
        rank.Link("a.py", "pkg/__init__.py", True, implicit=True),
        rank.Link("a.py", "pkg/mod.py", True),
        rank.Link("b.py", "pkg/__init__.py", True, implicit=True),
        rank.Link("b.py", "pkg/mod.py", True),
    ]
    r = rank.pagerank(list(files), links)
    assert r["pkg/mod.py"] > r["pkg/__init__.py"]
    assert r["pkg/__init__.py"] == pytest.approx(r["a.py"])
    out = rank.blast(["pkg/__init__.py"], files, links, r)
    assert {d["path"] for d in out["code"]} == {"a.py", "b.py"}


# --- blast radius ---------------------------------------------------------------


def _graph():
    #   app -> svc -> core ;  cli -> core ;  tests/test_svc -> svc ; tests/test_core -> core
    files = {
        "core.py": False,
        "svc.py": False,
        "app.py": False,
        "cli.py": False,
        "tests/test_svc.py": True,
        "tests/test_core.py": True,
    }
    edges = _e(
        ("svc.py", "core.py"),
        ("app.py", "svc.py"),
        ("cli.py", "core.py"),
        ("tests/test_svc.py", "svc.py"),
        ("tests/test_core.py", "core.py"),
    )
    return files, edges


def test_blast_finds_every_transitive_dependent_with_minimum_hops():
    files, edges = _graph()
    ranks = rank.pagerank(list(files), edges)
    out = rank.blast(["core.py"], files, edges, ranks)
    hops = {d["path"]: d["hops"] for d in out["code"]}
    assert hops == {"svc.py": 1, "cli.py": 1, "app.py": 2}
    assert {d["path"]: d["hops"] for d in out["tests"]} == {
        "tests/test_core.py": 1,
        "tests/test_svc.py": 2,
    }
    assert out["total"] == 5


def test_blast_sorts_by_rank_then_path_and_paginates():
    files, edges = _graph()
    ranks = rank.pagerank(list(files), edges)
    full = rank.blast(["core.py"], files, edges, ranks)
    order = [d["path"] for d in full["code"]]
    assert order == sorted(order, key=lambda p: (-ranks[p], p))
    page = rank.blast(["core.py"], files, edges, ranks, limit=1, offset=1)
    assert [d["path"] for d in page["code"]] == order[1:2]
    assert page["total"] == full["total"]
    assert page["code_total"] == 3


def test_seeds_are_excluded_even_through_a_cycle():
    files = {"a.py": False, "b.py": False}
    edges = _e(("a.py", "b.py"), ("b.py", "a.py"))
    out = rank.blast(["a.py"], files, edges, rank.pagerank(list(files), edges))
    assert [d["path"] for d in out["code"]] == ["b.py"]


def test_a_test_seed_is_itself_a_candidate_test():
    files, edges = _graph()
    out = rank.blast(["tests/test_core.py"], files, edges, rank.pagerank(list(files), edges))
    assert [d["path"] for d in out["tests"]] == ["tests/test_core.py"]
    assert out["tests"][0]["hops"] == 0


def test_certain_only_when_a_path_of_certain_edges_reaches_it():
    files = {"core.py": False, "a.py": False, "b.py": False}
    edges = [rank.Link("a.py", "core.py", False), rank.Link("b.py", "a.py", True)]
    out = rank.blast(["core.py"], files, edges, rank.pagerank(list(files), edges))
    certain = {d["path"]: d["certain"] for d in out["code"]}
    assert certain == {"a.py": False, "b.py": False}

    edges.append(rank.Link("b.py", "core.py", True))
    out = rank.blast(["core.py"], files, edges, rank.pagerank(list(files), edges))
    assert {d["path"]: d["certain"] for d in out["code"]} == {"a.py": False, "b.py": True}


def test_unknown_seeds_are_reported_not_fatal():
    files, edges = _graph()
    out = rank.blast(["nope.py", "core.py"], files, edges, rank.pagerank(list(files), edges))
    assert out["unknown_seeds"] == ["nope.py"]
    assert out["total"] == 5


# --- unreachable ------------------------------------------------------------------


def test_unreachable_from_roots():
    files = {"main.py": False, "used.py": False, "orphan.py": False, "tests/test_x.py": True}
    edges = _e(("main.py", "used.py"))
    out = rank.unreachable(files, edges, roots={"main.py", "tests/test_x.py"})
    assert out == ["orphan.py"]


def test_unreachable_is_none_without_roots():
    assert rank.unreachable({"a.py": False}, [], roots=set()) is None
