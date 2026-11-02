"""X22: the docs map - which docs cite a file, in this repo and in sibling checkouts.

A runbook names the scripts and configs it describes. X-ray already knows how
to find a path in prose and pin it to one repo file (X3's matcher); pointed at
the repo's own docs, and at the docs of the other checkouts on this machine,
it answers "which docs go stale when this file changes". Client side only:
nothing reaches the brain.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gingugu.xray import docs_map
from tests.test_xray_edit_hook import BASIC, _git, _repo

# --- pure: what a doc names -------------------------------------------------


def test_tokens_are_the_paths_a_doc_names():
    text = "Run `pkg/core.py`, then pkg/a.py. Not 0.40.0, not e.g., and pkg/a.py again."
    assert docs_map.tokens(text) == ["pkg/core.py", "pkg/a.py"]


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Acme/Widget/blob/main/lib/limits.py",
        "https://github.com/acme/widget/blob/v1.2/lib/limits.py#L10-L20",
        "see (https://github.com/acme/widget/tree/main/lib/limits.py).",
        "https://github.com/acme/widget/raw/abc123/lib/limits.py?plain=1",
    ],
)
def test_github_links_to_this_repo_name_its_paths(url):
    assert docs_map.github_paths(url, "acme/widget") == ["lib/limits.py"]


def test_github_links_elsewhere_name_nothing():
    text = "https://github.com/acme/other/blob/main/lib/limits.py"
    assert docs_map.github_paths(text, "acme/widget") == []


def test_a_repo_known_only_by_folder_name_never_matches_a_link():
    text = "https://github.com/acme/widget/blob/main/lib/limits.py"
    assert docs_map.github_paths(text, "widget") == []


def test_qualified_paths_are_prefixed_with_the_repo_name():
    text = "edit widget/lib/limits.py and Widget/conf/app.yaml; other/lib/x.py is not ours"
    assert docs_map.qualified(text, "acme/widget") == ["lib/limits.py", "conf/app.yaml"]


def test_qualified_includes_github_links():
    text = "https://github.com/acme/widget/blob/main/ops/run.sh"
    assert docs_map.qualified(text, "acme/widget") == ["ops/run.sh"]


# --- this repo's docs -------------------------------------------------------


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """No sibling search unless a test sets one up."""
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path / "no-roots"))


DOCS = {
    **BASIC,
    "x/util.py": "",
    "y/util.py": "",
    "docs/runbook.md": "Restart: edit `pkg/core.py`, then a.py. Shared: util.py.\n"
    "Retired: pkg/old.py\n",
    "README.md": "See README.md and docs/runbook.md.\n",
}


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, DOCS)


def test_a_doc_cites_the_files_it_names(repo, state, roots):
    m = docs_map.read(repo, state)
    assert docs_map.citing(m, "pkg/core.py") == [("docs/runbook.md", None)]
    assert docs_map.citing(m, "pkg/a.py") == [("docs/runbook.md", None)]  # unique suffix


def test_an_ambiguous_name_cites_nothing(repo, state, roots):
    m = docs_map.read(repo, state)
    assert docs_map.citing(m, "x/util.py") == []
    assert docs_map.citing(m, "y/util.py") == []


def test_a_doc_never_cites_itself(repo, state, roots):
    m = docs_map.read(repo, state)
    assert docs_map.citing(m, "README.md") == []
    assert docs_map.citing(m, "docs/runbook.md") == [("README.md", None)]


def test_a_path_no_file_has_cites_nothing_until_it_is_gone(repo, state, roots):
    m = docs_map.read(repo, state)
    assert docs_map.citing(m, "pkg/old.py") == []
    assert docs_map.citing_gone(m, "pkg/old.py") == [("docs/runbook.md", None)]


def test_a_deleted_file_is_still_named_by_the_docs_citing_it(repo, state, roots):
    (repo / "pkg/leaf.py").unlink()
    (repo / "docs/leaf.md").write_text("pkg/leaf.py does the thing\n")
    m = docs_map.read(repo, state)
    assert docs_map.citing_gone(m, "pkg/leaf.py") == [("docs/leaf.md", None)]


def test_untracked_docs_count(repo, state, roots):
    (repo / "docs/new.md").write_text("pkg/leaf.py\n")
    m = docs_map.read(repo, state)
    assert ("docs/new.md", None) in docs_map.citing(m, "pkg/leaf.py")


def test_a_github_link_to_this_repo_counts_in_its_own_docs(repo, state, roots):
    _git(repo, "remote", "add", "origin", "git@github.com:Acme/Repo.git")
    (repo / "docs/link.md").write_text(
        "[leaf](https://github.com/acme/repo/blob/main/pkg/leaf.py)\n"
    )
    m = docs_map.read(repo, state)
    assert ("docs/link.md", None) in docs_map.citing(m, "pkg/leaf.py")


def test_only_doc_files_are_read(repo, state, roots):
    (repo / "pkg/notes.py").write_text("# see pkg/leaf.py\n")
    m = docs_map.read(repo, state)
    assert docs_map.citing(m, "pkg/leaf.py") == []


def test_a_doc_past_the_byte_cap_is_read_only_up_to_it(repo, state, roots, monkeypatch):
    monkeypatch.setattr(docs_map, "MAX_DOC_BYTES", 64)
    (repo / "docs/long.md").write_text("x" * 100 + " pkg/leaf.py\n")
    m = docs_map.read(repo, state)
    assert docs_map.citing(m, "pkg/leaf.py") == []


# --- sibling checkouts --------------------------------------------------------


@pytest.fixture
def sibling(tmp_path, monkeypatch, repo):
    """``ops`` sits next to ``repo`` under one root; both are found by the root
    search, and ``repo`` itself is skipped."""
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(tmp_path))
    ops = tmp_path / "ops"
    ops.mkdir()
    _git(ops, "init", "-q")
    (ops / "docs").mkdir()
    (ops / "docs/run.md").write_text(
        "Deploy: repo/pkg/core.py. Local helper: pkg/a.py (ours, not repo's).\n"
    )
    _git(ops, "add", "-A")
    _git(ops, "commit", "-q", "-m", "init")
    return ops


def test_a_sibling_doc_cites_a_qualified_path(repo, state, sibling):
    m = docs_map.read(repo, state)
    assert ("docs/run.md", "ops") in docs_map.citing(m, "pkg/core.py")


def test_a_bare_path_in_a_sibling_means_the_siblings_own_file(repo, state, sibling):
    m = docs_map.read(repo, state)
    assert all(c.repo is None for c in docs_map.citing(m, "pkg/a.py"))


def test_a_sibling_github_link_cites_by_origin(repo, state, sibling):
    _git(repo, "remote", "add", "origin", "https://github.com/acme/repo.git")
    (sibling / "docs/gh.md").write_text("https://github.com/acme/repo/blob/main/pkg/leaf.py\n")
    _git(sibling, "add", "-A")
    _git(sibling, "commit", "-q", "-m", "gh")
    m = docs_map.read(repo, state)
    assert ("docs/gh.md", "ops") in docs_map.citing(m, "pkg/leaf.py")


def test_uncommitted_sibling_docs_are_not_read(repo, state, sibling):
    (sibling / "docs/draft.md").write_text("repo/pkg/leaf.py\n")
    m = docs_map.read(repo, state)
    assert docs_map.citing(m, "pkg/leaf.py") == []


def test_this_repo_is_never_its_own_sibling(repo, state, sibling):
    m = docs_map.read(repo, state)
    assert all(c.repo != "repo" for c in docs_map.citing(m, "pkg/core.py"))


def test_another_checkout_of_this_repo_is_not_a_sibling(repo, state, sibling, tmp_path):
    # A worktree or second clone of the same origin is this repo, not a neighbour.
    _git(repo, "remote", "add", "origin", "https://github.com/acme/repo.git")
    clone = tmp_path / "repo-2"
    _git(tmp_path, "clone", "-q", str(repo), str(clone))
    _git(clone, "remote", "set-url", "origin", "https://github.com/acme/repo.git")
    (clone / "docs/x.md").write_text("repo/pkg/leaf.py\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "x")
    m = docs_map.read(repo, state)
    assert "acme/repo" not in m.siblings
    assert docs_map.citing(m, "pkg/leaf.py") == []


# --- the cache ----------------------------------------------------------------


@pytest.fixture
def reads(monkeypatch):
    """Count every doc read through the module's one I/O seam."""
    seen: list[str] = []
    real = docs_map._read_text

    def counting(path):
        seen.append(str(path))
        return real(path)

    monkeypatch.setattr(docs_map, "_read_text", counting)
    return seen


def test_an_unchanged_repo_is_not_reread(repo, state, sibling, reads):
    docs_map.read(repo, state)
    assert reads
    reads.clear()
    m = docs_map.read(repo, state)
    assert reads == []
    assert ("docs/run.md", "ops") in docs_map.citing(m, "pkg/core.py")


def test_only_a_changed_doc_is_reread(repo, state, roots, reads):
    docs_map.read(repo, state)
    reads.clear()
    (repo / "docs/runbook.md").write_text("now pkg/leaf.py only, and a much longer line\n")
    m = docs_map.read(repo, state)
    assert [Path(p).name for p in reads] == ["runbook.md"]
    assert docs_map.citing(m, "pkg/leaf.py") == [("docs/runbook.md", None)]
    assert docs_map.citing(m, "pkg/core.py") == []


def test_a_sibling_is_reread_when_its_head_moves(repo, state, sibling, reads):
    docs_map.read(repo, state)
    reads.clear()
    (sibling / "docs/run.md").write_text("repo/pkg/leaf.py\n")
    _git(sibling, "commit", "-qam", "move")
    m = docs_map.read(repo, state)
    assert reads
    assert docs_map.citing(m, "pkg/leaf.py") == [("docs/run.md", "ops")]


def test_a_corrupt_cache_is_rebuilt(repo, state, roots):
    docs_map.read(repo, state)
    for f in state.glob("xray-docs-*.json"):
        f.write_text("{not json")
    m = docs_map.read(repo, state)
    assert docs_map.citing(m, "pkg/core.py") == [("docs/runbook.md", None)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_cache_is_owner_only(repo, state, roots):
    docs_map.read(repo, state)
    (cache,) = state.glob("xray-docs-*.json")
    assert cache.stat().st_mode & 0o077 == 0


def test_an_oversized_cache_is_ignored(repo, state, roots, monkeypatch):
    docs_map.read(repo, state)
    monkeypatch.setattr(docs_map, "MAX_CACHE_BYTES", 10)
    (cache,) = state.glob("xray-docs-*.json")
    assert docs_map._load(repo, state)[0] is None
    assert cache.exists()


# --- hostile input (security review) ------------------------------------------


def test_github_links_are_capped(monkeypatch):
    monkeypatch.setattr(docs_map, "MAX_TOKENS", 3)
    text = " ".join(f"https://github.com/a/w/blob/main/f{i}.py" for i in range(50))
    assert len(docs_map.github_paths(text, "a/w")) == 3


@pytest.mark.parametrize("name", ["/etc/passwd.md", "../outside.md", "docs/../../x.md", ""])
def test_a_doc_name_that_leaves_the_checkout_is_refused(tmp_path, name):
    assert docs_map._inside(tmp_path, name) is None


def test_a_doc_name_inside_is_accepted(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("x")
    assert docs_map._inside(tmp_path, "docs/a.md") == tmp_path / "docs/a.md"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_a_doc_under_a_symlinked_directory_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_text("x")
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "docs").symlink_to(outside)
    assert docs_map._inside(repo, "docs/a.md") is None


def test_a_sibling_with_an_invalid_identity_is_skipped(repo, state, sibling, tmp_path):
    odd = tmp_path / "x, evil"
    sibling.rename(odd)
    m = docs_map.read(repo, state)
    assert m.siblings == {}


def test_siblings_stop_at_the_time_budget(repo, state, sibling, monkeypatch):
    monkeypatch.setattr(docs_map, "SIBLING_BUDGET_S", 0.0)
    m = docs_map.read(repo, state)
    assert m.siblings == {}  # nothing read, nothing cached, the brief goes on


def test_a_sibling_cached_before_the_budget_ran_out_is_kept(repo, state, sibling, monkeypatch):
    docs_map.read(repo, state)
    monkeypatch.setattr(docs_map, "SIBLING_BUDGET_S", 0.0)
    m = docs_map.read(repo, state)
    assert ("docs/run.md", "ops") in docs_map.citing(m, "pkg/core.py")


# --- the switch ---------------------------------------------------------------


@pytest.mark.parametrize("value,on", [(None, True), ("on", True), ("off", False), ("0", False)])
def test_the_switch(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv("MEMORY_XRAY_DOCS", raising=False)
    else:
        monkeypatch.setenv("MEMORY_XRAY_DOCS", value)
    assert docs_map.enabled() is on
