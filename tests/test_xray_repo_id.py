"""X20 part 2: an ``xray:`` line can name its repo, so a receiver finds the checkout.

A receiver's cwd is often not the repo the post is about (a Codex watcher runs
from its thread directory). The sender stamps ``xray: <repo> <range>``; the
receiver uses its cwd when that checkout is the repo, else looks for exactly
``<root>/<name>`` under ``MEMORY_XRAY_REPOS`` (or the usual places), and only
uses a candidate whose identity matches. The repo is another agent's text: it
is gated before it touches a path.
"""

from __future__ import annotations

import os

import pytest

from gingugu.xray import board_brief, repo_id
from tests.test_xray_brief import branched, repo  # noqa: F401 - fixtures
from tests.test_xray_edit_hook import _git

# --- parse ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, want",
    [
        ("xray: main..feature/x", (None, "main..feature/x")),
        ("hi\nxray: gingugu/gingugu main..feature/x\n", ("gingugu/gingugu", "main..feature/x")),
        ("xray: Acme/Widget HEAD~1..HEAD", ("Acme/Widget", "HEAD~1..HEAD")),
        ("xray: widget HEAD~1..HEAD", ("widget", "HEAD~1..HEAD")),
        ("xray: ../etc HEAD~1..HEAD", None),
        ("xray: a/b/c HEAD~1..HEAD", None),
        ("xray: a..b HEAD", None),
        ("xray: -x HEAD~1..HEAD", None),
        ("xray: .hidden HEAD~1..HEAD", None),
        ("xray: a/.. HEAD~1..HEAD", None),
        ("xray: " + "a" * 101 + " HEAD~1..HEAD", None),
        ("xray: ok/repo --output=/tmp/x", None),
        ("xray: a b c", None),
    ],
)
def test_parse_takes_an_optional_gated_repo_before_the_range(body, want):
    assert board_brief.parse(body) == want


def test_ref_in_still_returns_only_the_range():
    assert board_brief.ref_in("xray: gingugu/gingugu main..x") == "main..x"


# --- identity --------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, want",
    [
        ("git@github.com:gingugu/gingugu.git", "gingugu/gingugu"),
        ("https://github.com/Gingugu/Gingugu.git", "gingugu/gingugu"),
        ("https://github.com/gingugu/gingugu", "gingugu/gingugu"),
        ("ssh://git@gitlab.example.com:2222/team/proj.git", "team/proj"),
        ("https://user:pw@host/team/proj/", "team/proj"),
        ("/srv/git/proj.git", "git/proj"),
        ("", None),
        ("not a url", None),
    ],
)
def test_normalize_reduces_a_remote_to_owner_slash_name(url, want):
    assert repo_id.normalize(url) == want


def test_of_is_origin_when_set_else_the_checkout_name(repo):  # noqa: F811
    assert repo_id.of(repo) == "repo"
    _git(repo, "remote", "add", "origin", "git@github.com:acme/widget.git")
    assert repo_id.of(repo) == "acme/widget"


def test_matches_compares_case_blind_and_a_bare_name_matches_the_name_part(repo):  # noqa: F811
    _git(repo, "remote", "add", "origin", "git@github.com:acme/widget.git")
    assert repo_id.matches(repo, "ACME/widget")
    assert repo_id.matches(repo, "widget")
    assert not repo_id.matches(repo, "other/widget")
    assert not repo_id.matches(repo, "repo")  # origin wins over the folder name


# --- roots ---------------------------------------------------------------------------


def test_roots_come_from_the_env_and_drop_what_is_missing(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    env = os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "missing"), str(tmp_path / "a")])
    monkeypatch.setenv("MEMORY_XRAY_REPOS", env)
    assert repo_id.roots() == [tmp_path / "a"]


def test_an_empty_env_switches_the_search_off(monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_REPOS", "")
    assert repo_id.roots() == []


def test_the_default_roots_are_existing_folders_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_XRAY_REPOS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    (tmp_path / "src").mkdir()
    assert repo_id.roots() == [tmp_path / "src"]


# --- find --------------------------------------------------------------------------------


def test_find_prefers_a_matching_cwd(repo, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MEMORY_XRAY_REPOS", "")
    assert repo_id.find("repo", str(repo / "pkg")) == repo


def test_find_looks_for_exactly_root_slash_name(repo, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path))
    (tmp_path / "elsewhere").mkdir()
    assert repo_id.find("repo", str(tmp_path / "elsewhere")) == repo
    assert repo_id.find("repo", None) == repo
    assert repo_id.find("nope", None) is None


def test_find_never_uses_a_checkout_whose_identity_differs(
    repo, tmp_path, monkeypatch  # noqa: F811
):
    _git(repo, "remote", "add", "origin", "git@github.com:acme/repo.git")
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path))
    assert repo_id.find("acme/repo", None) == repo
    assert repo_id.find("other/repo", None) is None
    assert repo_id.find("other/repo", str(repo)) is None  # not even from the cwd


def test_find_refuses_an_ungated_name(repo, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "repo"))
    assert repo_id.find("..", None) is None
    assert repo_id.find("../repo", None) is None


# --- attach ---------------------------------------------------------------------------------


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "data" / "x.db"))


def test_attach_renders_from_the_named_repo_when_cwd_is_elsewhere(
    branched, state, tmp_path, monkeypatch  # noqa: F811
):
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path))
    (tmp_path / "codex-thread").mkdir()
    text = board_brief.attach("xray: repo HEAD~1..HEAD", str(tmp_path / "codex-thread"), 4000)
    assert text is not None
    assert "GINGUGU X-RAY BRIEF:" in text and "pkg/a.py" in text
    assert "repo" in text.splitlines()[0] and "HEAD~1..HEAD" in text.splitlines()[0]


def test_attach_names_the_repo_when_no_checkout_of_it_is_found(state, tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path))
    text = board_brief.attach("xray: acme/widget main..feature/x", str(tmp_path), 4000)
    assert text is not None and "\n" not in text and len(text) < 300
    assert "acme/widget" in text
    assert "`gingugu xray brief --range main..feature/x`" in text


def test_attach_does_not_brief_from_a_cwd_that_is_another_repo(
    branched, state, monkeypatch  # noqa: F811
):
    monkeypatch.setenv("MEMORY_XRAY_REPOS", "")
    text = board_brief.attach("xray: acme/widget HEAD~1..HEAD", str(branched), 4000)
    assert text is not None and "GINGUGU X-RAY BRIEF:" not in text
    assert "acme/widget" in text
