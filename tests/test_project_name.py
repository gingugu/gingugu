"""A git worktree's project namespace is its main checkout's.

The hooks name the project namespace after the session's folder. A linked
worktree (``git worktree add``, Claude Code's ``.claude/worktrees/<name>``)
names a branch, not a repo, so recall, tripwires and board mail asked for a
namespace nobody writes to. Its ``.git`` is a file pointing into
``<main>/.git/worktrees/<name>``: read that, never spawn git, since the prompt
and tool hooks run on every prompt and every tool call.
"""

from __future__ import annotations

import subprocess

import pytest

from gingugu import prompt_hook
from gingugu.project_name import project_name


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "myrepo"
    main.mkdir()
    _git(main, "init", "-q")
    (main / "a.py").write_text("")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "i")
    return main


def test_a_main_checkout_is_its_folder(repo):
    assert project_name(str(repo)) == "myrepo"


def test_a_worktree_is_its_main_checkout(repo, tmp_path):
    wt = tmp_path / "elsewhere" / "feature-x"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert project_name(str(wt)) == "myrepo"


def test_a_worktree_under_the_repo_is_its_main_checkout(repo):
    wt = repo / ".claude" / "worktrees" / "fix-y"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert project_name(str(wt)) == "myrepo"


def test_a_relative_gitdir_is_resolved(repo, tmp_path):
    wt = tmp_path / "rel"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: ../myrepo/.git/worktrees/rel\n")
    assert project_name(str(wt)) == "myrepo"


def test_a_submodule_keeps_its_own_name(tmp_path):
    sub = tmp_path / "parent" / "vendored"
    sub.mkdir(parents=True)
    (sub / ".git").write_text("gitdir: ../.git/modules/vendored\n")
    assert project_name(str(sub)) == "vendored"


@pytest.mark.parametrize("text", ["", "nonsense", "gitdir:", "gitdir: \x00", "gitdir: /"])
def test_an_odd_git_file_falls_back_to_the_folder(tmp_path, text):
    here = tmp_path / "plain"
    here.mkdir()
    (here / ".git").write_text(text)
    assert project_name(str(here)) == "plain"


def test_no_git_at_all_is_the_folder(tmp_path):
    assert project_name(str(tmp_path / "nope")) == "nope"


def test_the_hooks_namespaces_follow_the_main_checkout(repo, tmp_path):
    wt = tmp_path / "wt" / "branch-z"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert prompt_hook.namespaces_for(str(wt)) == ["crow", "myrepo"]


def test_the_session_start_contract_names_the_main_checkout(repo, tmp_path):
    import importlib.util

    from gingugu.bootstrap import _read_template

    script = tmp_path / "session_start.py"
    script.write_text(_read_template("session_start.py.tmpl"))
    spec = importlib.util.spec_from_file_location("ss_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    wt = tmp_path / "wt2" / "branch-q"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert 'namespace="crow,myrepo"' in mod.build_startup_contract(str(wt))
