"""X19: the X-ray brief - what a change touches and what it reaches, for a handoff.

An agent that never fires the read or edit hooks (one that reads through a
shell and edits with a patch tool) still gets the map of a task: the changed
files by rank, and the combined blast of the changed source. Computed on this
machine from the same cached graph the hooks use; never touches the brain.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import brief, hook_common
from gingugu.xray.scan import XrayError
from tests.test_xray_edit_hook import BASIC, _git, _repo


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, BASIC)


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


@pytest.fixture
def branched(repo):
    """One commit on top of BASIC: M core, A new + README, D leaf, R b -> b2."""
    (repo / "pkg/core.py").write_text("X = 2\n")
    (repo / "pkg/new.py").write_text("from pkg import core\n")
    (repo / "README.md").write_text("hi\n")
    (repo / "pkg/leaf.py").unlink()
    (repo / "pkg/b.py").rename(repo / "pkg/b2.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "work")
    return repo


def _rows(changes) -> set[tuple]:
    return {(c.status, c.path, c.old_path) for c in changes}


# --- changes -------------------------------------------------------------


def test_a_range_names_every_status_and_the_rename_source(branched):
    got = brief.changes(branched, rev_range="HEAD~1..HEAD")
    assert _rows(got) == {
        ("M", "pkg/core.py", None),
        ("A", "pkg/new.py", None),
        ("A", "README.md", None),
        ("D", "pkg/leaf.py", None),
        ("R", "pkg/b2.py", "pkg/b.py"),
    }
    assert [c.path for c in got] == sorted(c.path for c in got)


def test_no_range_is_the_working_tree_against_head_with_untracked(repo):
    (repo / "pkg/a.py").write_text("from pkg import core\nZ = 1\n")
    (repo / "pkg/staged.py").write_text("")
    _git(repo, "add", "pkg/staged.py")
    (repo / "pkg/untracked.py").write_text("")
    assert _rows(brief.changes(repo)) == {
        ("M", "pkg/a.py", None),
        ("A", "pkg/staged.py", None),
        ("A", "pkg/untracked.py", None),
    }


def test_a_clean_tree_has_no_changes(repo):
    assert brief.changes(repo) == []


def test_named_files_are_taken_as_given(repo):
    got = brief.changes(repo, files=["pkg/core.py", "pkg/gone.py"])
    assert _rows(got) == {("M", "pkg/core.py", None), ("D", "pkg/gone.py", None)}


@pytest.mark.parametrize("bad", ["-x", "--output=/tmp/x", "a b", "a\tb", "a\nb", "a\x00b", ""])
def test_a_range_that_could_be_an_option_or_smuggle_text_is_refused(repo, bad):
    with pytest.raises(ValueError):
        brief.changes(repo, rev_range=bad)


def test_an_unknown_revision_is_an_xray_error(repo):
    with pytest.raises(XrayError):
        brief.changes(repo, rev_range="HEAD..no-such-branch")


# --- build ---------------------------------------------------------------


def test_build_ranks_the_changed_files_and_blasts_the_changed_source(branched, state):
    g = hook_common.graph(branched, state)
    data = brief.build(g, brief.changes(branched, rev_range="HEAD~1..HEAD"))
    assert data["counts"] == {"A": 2, "M": 1, "D": 1, "R": 1}
    by_path = {c["path"]: c for c in data["changed"]}
    assert by_path["pkg/core.py"]["source"] is True
    assert by_path["pkg/core.py"]["importers"] == 3  # a, b2, new
    assert by_path["pkg/b2.py"]["old_path"] == "pkg/b.py"
    assert by_path["pkg/leaf.py"]["source"] is False  # deleted: not in today's graph
    assert by_path["README.md"]["source"] is False
    assert data["other"] == 1  # README; a delete is not "other"
    source = [c for c in data["changed"] if c["source"]]
    assert [c["rank"] for c in source] == sorted(c["rank"] for c in source)
    assert data["changed"][: len(source)] == source  # source first, by rank
    # Seeds are not their own blast: only a.py is reached, and its test.
    assert data["blast"]["code"] == ["pkg/a.py"]
    assert data["blast"]["tests"] == ["tests/test_a.py"]
    assert data["blast"]["code_total"] == 1 and data["blast"]["tests_total"] == 1
    assert data["files"] == len([p for p in g.files if p not in g.packages])
    assert 0 < len(data["top"]) <= 5


def test_a_changed_test_is_itself_a_test_to_run(repo, state):
    g = hook_common.graph(repo, state)
    data = brief.build(g, [brief.Change("M", "tests/test_a.py", None)])
    assert data["blast"]["tests"] == ["tests/test_a.py"]


def test_build_with_no_changes_is_empty_not_an_error(repo, state):
    data = brief.build(hook_common.graph(repo, state), [])
    assert data["changed"] == [] and data["blast"]["code"] == [] and data["other"] == 0


def test_limit_pages_the_blast_lists_but_not_the_totals(repo, state):
    g = hook_common.graph(repo, state)
    data = brief.build(g, [brief.Change("M", "pkg/core.py", None)], limit=1)
    assert len(data["blast"]["code"]) == 1
    assert data["blast"]["code_total"] == 2  # a, b


# --- render --------------------------------------------------------------


def test_render_names_the_change_and_what_it_reaches(branched, state):
    g = hook_common.graph(branched, state)
    text = brief.render(brief.build(g, brief.changes(branched, rev_range="HEAD~1..HEAD")), "repo")
    assert text.startswith("GINGUGU X-RAY BRIEF: repo")
    for path in ("pkg/core.py", "pkg/new.py", "pkg/leaf.py", "pkg/a.py", "tests/test_a.py"):
        assert path in text
    assert "pkg/b.py -> pkg/b2.py" in text


def test_render_of_nothing_says_so(repo, state):
    text = brief.render(brief.build(hook_common.graph(repo, state), []), "repo")
    assert text.startswith("GINGUGU X-RAY BRIEF: repo")
    assert "no changes" in text


def test_render_of_a_huge_change_stays_under_the_cap(repo, state):
    data = brief.build(hook_common.graph(repo, state), [])
    data["changed"] = [
        {
            "path": f"pkg/f{i}.py",
            "status": "A",
            "old_path": None,
            "source": False,
            "rank": None,
            "importers": 0,
        }
        for i in range(3000)
    ]
    data["changed"].append(
        {
            "path": "evil\nname.py",
            "status": "A",
            "old_path": None,
            "source": False,
            "rank": None,
            "importers": 0,
        }
    )
    data["counts"]["A"] = len(data["changed"])
    text = brief.render(data, "repo")
    assert len(text) <= brief.MAX_CHARS
    assert "name.py" not in text.splitlines()


def test_render_quotes_a_hostile_name_it_shows(repo, state):
    data = brief.build(hook_common.graph(repo, state), [brief.Change("A", "evil\nname.py", None)])
    text = brief.render(data, "repo")
    assert json.dumps("evil\nname.py") in text
    assert not any(line.startswith("name.py") for line in text.splitlines())


# --- CLI -----------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    from gingugu import xray_cli

    return xray_cli


def test_cli_prints_the_brief_for_a_range(branched, cli_env, capsys):
    rc = cli_env.main(["brief", "--range", "HEAD~1..HEAD", "--path", str(branched)])
    assert rc == 0
    assert capsys.readouterr().out.startswith("GINGUGU X-RAY BRIEF:")


def test_cli_json_and_files(repo, cli_env, capsys):
    rc = cli_env.main(["brief", "--files", "pkg/core.py,pkg/a.py", "--path", str(repo), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert {c["path"] for c in out["changed"]} == {"pkg/core.py", "pkg/a.py"}
    assert "text" in out


@pytest.mark.parametrize(
    "argv",
    [[], ["nope"], ["brief", "--range", "a", "--files", "b"], ["brief", "--bogus"]],
)
def test_cli_usage_errors_exit_2(cli_env, argv, capsys):
    assert cli_env.main(argv) == 2


def test_cli_outside_a_repo_exits_1(tmp_path, cli_env, capsys):
    (tmp_path / "plain").mkdir()
    assert cli_env.main(["brief", "--path", str(tmp_path / "plain")]) == 1
    assert "git" in capsys.readouterr().err


def test_gingugu_xray_dispatches_to_the_cli(branched, cli_env, monkeypatch, capsys):
    import sys

    from gingugu.server import main

    monkeypatch.setattr(
        sys,
        "argv",
        ["gingugu", "xray", "brief", "--range", "HEAD~1..HEAD", "--path", str(branched)],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert "GINGUGU X-RAY BRIEF" in capsys.readouterr().out
