"""X4 beyond the import graph: churn and coupling for every tracked file.

A YAML/HCL repo has no import graph, so hotspots and coupling used to come out
empty however busy its history: a busy YAML repo reported 0 of
both. Files outside the graph are now:

- ranked in their own list, ``others``, by churn x authors - import rank means
  nothing for a workflow file, so they are never mixed into the ranked list;
- coupled like any other pair, where ``hidden`` just means neither imports the
  other, which is always true outside the graph.

Only tracked files count: ``History.churn`` also holds files deleted since.
``tracked=None`` keeps the graph-only behaviour, for a caller that cannot list.
"""

from __future__ import annotations

import pytest

from gingugu.xray import history_tool, hotspots
from tests.test_xray_edit_hook import BASIC, _git, _repo
from tests.test_xray_hotspots import FILES, LINKS, _graph, _hist

TRACKED = {*FILES, "deploy.yml", "vend.yml", "main.tf", "README.md"}


@pytest.fixture
def g():
    return _graph(FILES, LINKS)


# --- others: ranked outside the graph ---------------------------------------------------


def test_others_rank_tracked_files_outside_the_graph_by_churn_times_authors(g):
    h = _hist(
        {"deploy.yml": 47, "vend.yml": 40, "main.tf": 10, "x.py": 30, "gone.yml": 99},
        authors={"deploy.yml": 1, "vend.yml": 3, "main.tf": 2, "x.py": 1, "gone.yml": 5},
    )
    rows = hotspots.others(g, h, TRACKED)
    assert [r["path"] for r in rows] == ["vend.yml", "deploy.yml", "main.tf"]  # 120, 47, 20
    assert rows[0] == {"path": "vend.yml", "commits": 40, "authors": 3}


def test_others_skip_graph_files_untouched_files_and_deleted_files(g):
    h = _hist({"x.py": 5, "gone.yml": 5, "README.md": 0})
    assert hotspots.others(g, h, TRACKED) == []


def test_others_break_ties_by_path(g):
    h = _hist({"vend.yml": 5, "deploy.yml": 5})
    assert [r["path"] for r in hotspots.others(g, h, TRACKED)] == ["deploy.yml", "vend.yml"]


def test_the_ranked_list_is_unchanged_by_tracked_files(g):
    h = _hist({"x.py": 10, "deploy.yml": 50})
    assert [r["path"] for r in hotspots.hotspots(g, h)] == ["x.py"]


# --- coupling over every tracked file ---------------------------------------------------


def test_two_workflow_files_that_change_together_couple(g):
    h = _hist({"deploy.yml": 20, "vend.yml": 20}, pairs={("deploy.yml", "vend.yml"): 12})
    (row,) = hotspots.coupling(g, h, tracked=TRACKED)
    assert (row["a"], row["b"], row["shared"], row["hidden"]) == (
        "deploy.yml",
        "vend.yml",
        12,
        True,
    )
    assert row["degree"] == pytest.approx(0.6)


def test_a_graph_file_couples_with_a_tracked_file(g):
    h = _hist({"x.py": 10, "main.tf": 10}, pairs={("main.tf", "x.py"): 8})
    assert [(r["a"], r["b"]) for r in hotspots.coupling(g, h, tracked=TRACKED)] == [
        ("main.tf", "x.py")
    ]


def test_a_deleted_file_never_couples(g):
    h = _hist({"x.py": 10, "gone.yml": 10}, pairs={("gone.yml", "x.py"): 8})
    assert hotspots.coupling(g, h, tracked=TRACKED) == []


def test_without_a_listing_coupling_stays_inside_the_graph(g):
    h = _hist({"deploy.yml": 20, "vend.yml": 20}, pairs={("deploy.yml", "vend.yml"): 12})
    assert hotspots.coupling(g, h) == []


def test_thresholds_still_apply_outside_the_graph(g):
    h = _hist({"deploy.yml": 4, "vend.yml": 20}, pairs={("deploy.yml", "vend.yml"): 4})
    assert hotspots.coupling(g, h, tracked=TRACKED) == []


def test_focus_applies_outside_the_graph(g):
    h = _hist(
        {"deploy.yml": 20, "vend.yml": 20, "x.py": 20, "y.py": 20},
        pairs={("deploy.yml", "vend.yml"): 12, ("x.py", "y.py"): 12},
    )
    rows = hotspots.coupling(g, h, files=["vend.yml"], tracked=TRACKED)
    assert [(r["a"], r["b"]) for r in rows] == [("deploy.yml", "vend.yml")]


# --- the tool, end to end ---------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    """Two workflow files that change together 6 times; one deleted after 6 commits."""
    r = _repo(tmp_path, {**BASIC, "ci/deploy.yml": "0\n", "ci/vend.yml": "0\n", "old.yml": "0\n"})
    for i in range(6):
        for name in ("ci/deploy.yml", "ci/vend.yml", "old.yml"):
            (r / name).write_text(f"{i + 1}\n")
        _git(r, "commit", "-qam", f"ci {i}")
    _git(r, "rm", "-q", "old.yml")
    _git(r, "commit", "-qm", "drop old")
    return r


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))


def test_the_tool_reports_others_and_their_coupling(repo, env):
    out = history_tool.answer(str(repo), "history")
    assert [r["path"] for r in out["others"]] == ["ci/deploy.yml", "ci/vend.yml"]
    assert out["others_total"] == 2
    pairs = [(c["a"], c["b"]) for c in out["coupling"]]
    assert ("ci/deploy.yml", "ci/vend.yml") in pairs
    assert all("old.yml" not in (c["a"], c["b"]) for c in out["coupling"])
    assert "Other changed files (churn x authors" in out["text"]
    assert "ci/deploy.yml <-> ci/vend.yml" in out["text"]


def test_limit_pages_others(repo, env):
    out = history_tool.answer(str(repo), "history", limit=1)
    assert len(out["others"]) == 1 and out["others_total"] == 2
