"""X4 hotspots and coupling, X6 blind spots: the import graph read against history.

Pure functions over a ``hook_common.Graph`` and a ``history.History``. Coupling
uses code-maat's published defaults: both files changed in at least
``MIN_REVS`` commits, together in at least ``MIN_SHARED``, and a degree (shared
over the average of the two revision counts) of at least ``MIN_DEGREE``.
"""

from __future__ import annotations

import pytest

from gingugu.xray import history, hotspots, rank
from gingugu.xray.hook_common import Graph


def _graph(files: dict[str, bool], links=(), packages=()) -> Graph:
    links = [rank.Link(s, d) for s, d in links]
    return Graph(
        files,
        links,
        rank.pagerank(sorted(files), links),
        set(packages),
        dict.fromkeys(files, "python"),
        set(),
    )


def _hist(churn, authors=None, pairs=None, commits=100) -> history.History:
    return history.History(
        head="h" * 40,
        commits=commits,
        bulk=0,
        churn=dict(churn),
        authors=dict(authors or dict.fromkeys(churn, 1)),
        pairs={tuple(sorted(k)): v for k, v in (pairs or {}).items()},
    )


# a and b import core; t/test_a.py imports a; x and y never import each other.
FILES = {
    "core.py": False,
    "a.py": False,
    "b.py": False,
    "x.py": False,
    "y.py": False,
    "t/test_a.py": True,
}
LINKS = [("a.py", "core.py"), ("b.py", "core.py"), ("t/test_a.py", "a.py")]


@pytest.fixture
def g():
    return _graph(FILES, LINKS)


# --- hotspots ------------------------------------------------------------


def test_hotspots_order_by_churn_times_rank_times_authors(g):
    h = _hist({"core.py": 10, "a.py": 10, "x.py": 30}, {"core.py": 3, "a.py": 1, "x.py": 1})
    rows = hotspots.hotspots(g, h)
    scores = {r["path"]: g.ranks[r["path"]] * r["commits"] * r["authors"] for r in rows}
    assert [r["path"] for r in rows] == sorted(scores, key=lambda p: (-scores[p], p))
    assert rows[0]["path"] == "core.py"


def test_hotspots_skip_tests_packages_and_untouched_files():
    g = _graph({**FILES, "pkg": False}, LINKS, packages={"pkg"})
    h = _hist({"t/test_a.py": 50, "pkg": 50, "a.py": 1})
    assert [r["path"] for r in hotspots.hotspots(g, h)] == ["a.py"]


def test_a_hotspot_row_names_its_rank_position(g):
    rows = hotspots.hotspots(g, _hist({"core.py": 1}))
    assert rows[0]["rank"] == 1  # core.py is the most leaned-on file
    assert set(rows[0]) == {"path", "rank", "commits", "authors", "importers"}


# --- coupling ------------------------------------------------------------


def test_degree_is_shared_over_the_average_revisions(g):
    h = _hist({"x.py": 10, "y.py": 6}, pairs={("x.py", "y.py"): 6})
    (row,) = hotspots.coupling(g, h)
    assert row["degree"] == pytest.approx(6 / 8)
    assert (row["a"], row["b"], row["shared"]) == ("x.py", "y.py", 6)
    assert row["hidden"] is True


def test_an_import_either_way_is_not_hidden(g):
    h = _hist({"a.py": 5, "core.py": 5}, pairs={("a.py", "core.py"): 5})
    (row,) = hotspots.coupling(g, h)
    assert row["hidden"] is False


@pytest.mark.parametrize(
    "churn, shared",
    [
        ({"x.py": 4, "y.py": 9}, 4),  # x under MIN_REVS
        ({"x.py": 9, "y.py": 9}, 4),  # shared under MIN_SHARED (never cached, but guard)
        ({"x.py": 40, "y.py": 10}, 7),  # 7 / 25 = 28% < 30%
    ],
)
def test_coupling_thresholds(g, churn, shared):
    assert hotspots.coupling(g, _hist(churn, pairs={("x.py", "y.py"): shared})) == []


def test_exactly_thirty_percent_couples(g):
    h = _hist({"x.py": 25, "y.py": 25}, pairs={("x.py", "y.py"): 8})  # 8/25 = 32%
    assert len(hotspots.coupling(g, h)) == 1
    h = _hist({"x.py": 20, "y.py": 20}, pairs={("x.py", "y.py"): 6})  # 6/20 = 30%
    assert len(hotspots.coupling(g, h)) == 1


def test_coupling_keeps_only_files_in_the_graph(g):
    h = _hist({"x.py": 5, "README.md": 5}, pairs={("README.md", "x.py"): 5})
    assert hotspots.coupling(g, h) == []


def test_coupling_orders_by_degree_then_shared_then_names(g):
    h = _hist(
        {"x.py": 10, "y.py": 10, "b.py": 10, "a.py": 10},
        pairs={("x.py", "y.py"): 5, ("a.py", "b.py"): 8, ("b.py", "x.py"): 5},
    )
    got = [(r["a"], r["b"]) for r in hotspots.coupling(g, h)]
    assert got == [("a.py", "b.py"), ("b.py", "x.py"), ("x.py", "y.py")]


def test_coupling_focused_on_named_files(g):
    h = _hist(
        {"x.py": 10, "y.py": 10, "a.py": 10, "b.py": 10},
        pairs={("x.py", "y.py"): 5, ("a.py", "b.py"): 8},
    )
    assert [(r["a"], r["b"]) for r in hotspots.coupling(g, h, files=["y.py"])] == [("x.py", "y.py")]


def test_partners_are_the_hidden_couplings_of_one_file_strongest_first(g):
    h = _hist(
        {"x.py": 10, "y.py": 10, "b.py": 10, "a.py": 10},
        pairs={("x.py", "y.py"): 5, ("b.py", "x.py"): 9, ("a.py", "x.py"): 9},
    )
    # a.py and x.py: no import, hidden. b.py and x.py: hidden too, same degree.
    assert hotspots.partners(g, h, "x.py") == [("a.py", 0.9), ("b.py", 0.9), ("y.py", 0.5)]
    assert hotspots.partners(g, h, "core.py") == []


# --- tested --------------------------------------------------------------


def test_tested_by_a_direct_test_importer_or_a_test_named_for_it():
    files = {
        "pkg/_urls.py": False,
        "pkg/models/__init__.py": False,
        "pkg/plain.py": False,
        "tests/test_urls.py": True,
        "tests/test_models.py": True,
        "tests/test_other.py": True,
    }
    g = _graph(files, [("tests/test_other.py", "pkg/plain.py")])
    assert hotspots.tested(g) == {"pkg/_urls.py", "pkg/models/__init__.py", "pkg/plain.py"}


def test_a_test_that_only_shares_a_word_does_not_count():
    g = _graph({"pkg/urls.py": False, "tests/test_parse_urls.py": True})
    assert hotspots.tested(g) == set()


# --- blind spots ---------------------------------------------------------


def test_blind_spots_are_untested_imported_source_by_rank_over_authors():
    files = {
        "hub.py": False,  # 3 importers, untested, 4 authors
        "mid.py": False,  # 1 importer, untested, 1 author
        "leaf.py": False,  # nothing imports it: a change reaches nothing
        "seen.py": False,  # imported and tested
        "u1.py": False,
        "u2.py": False,
        "tests/test_seen.py": True,
    }
    links = [
        ("u1.py", "hub.py"),
        ("u2.py", "hub.py"),
        ("seen.py", "hub.py"),
        ("u1.py", "mid.py"),
        ("u1.py", "seen.py"),
        ("tests/test_seen.py", "seen.py"),
    ]
    g = _graph(files, links)
    h = _hist({p: 3 for p in files}, {**dict.fromkeys(files, 1), "hub.py": 4})
    rows = hotspots.blindspots(g, h)
    paths = [r["path"] for r in rows]
    assert "leaf.py" not in paths and "seen.py" not in paths
    assert set(paths) == {"hub.py", "mid.py"}
    expect = sorted(paths, key=lambda p: -g.ranks[p] / h.authors[p])
    assert paths == expect
    row = next(r for r in rows if r["path"] == "mid.py")
    assert row["single_author"] is True and row["importers"] == 1 and row["commits"] == 3


def test_a_blind_spot_tie_goes_to_the_file_changed_least():
    files = {"a.py": False, "b.py": False, "c.py": False}
    g = _graph(files, [("c.py", "a.py"), ("c.py", "b.py")])
    h = _hist({"a.py": 9, "b.py": 2, "c.py": 1})
    assert [r["path"] for r in hotspots.blindspots(g, h)] == ["b.py", "a.py"]


def test_a_file_outside_the_window_is_a_blind_spot_with_no_commits():
    g = _graph({"a.py": False, "b.py": False}, [("b.py", "a.py")])
    (row,) = hotspots.blindspots(g, _hist({}))
    assert row["path"] == "a.py"
    assert row["commits"] == 0 and row["authors"] == 0 and row["single_author"] is False


def test_packages_and_init_files_are_never_blind_spots():
    files = {"pkg": False, "pkg/__init__.py": False, "user.py": False}
    g = _graph(files, [("user.py", "pkg"), ("user.py", "pkg/__init__.py")], packages={"pkg"})
    assert hotspots.blindspots(g, _hist({})) == []
