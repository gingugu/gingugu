"""X3 scar tissue, the client's half: what a file has cost before.

The brain sends back the memories in this repo's project namespace that name a
file's basename, with every path-like token each one names. Here those tokens
are resolved against the checkout: a memory counts only when one of its tokens
is this file (the full path, or a suffix no other file shares). It is scored by
type - a bug outweighs a decision, a decision a fact - divided by how many repo
files it names, since a note about one file is about it and a plan naming
thirty is not. Recency breaks ties.
"""

from __future__ import annotations

import subprocess

import pytest

from gingugu.xray import scars


@pytest.fixture(autouse=True)
def scars_on(monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_SCARS", "on")


FILES = {
    "src/pkg/storage.py",
    "src/pkg/search.py",
    "ui/search.py",
    "src/pkg/rank.py",
    "tests/test_storage.py",
}


def _hit(
    id="m-1", type="bug", title="t", created="2026-10-01T00:00:00", paths=("src/pkg/storage.py",)
):
    return {"id": id, "type": type, "title": title, "created_at": created, "paths": list(paths)}


# --- rank: which hits are about the file -------------------------------------------


def test_a_full_path_to_the_file_counts():
    (s,) = scars.rank([_hit()], "src/pkg/storage.py", FILES)
    assert s.id == "m-1" and s.named == 1


def test_a_unique_suffix_counts():
    assert len(scars.rank([_hit(paths=["storage.py"])], "src/pkg/storage.py", FILES)) == 1
    assert len(scars.rank([_hit(paths=["pkg/storage.py"])], "src/pkg/storage.py", FILES)) == 1


def test_a_name_two_files_share_is_not_about_either():
    assert scars.rank([_hit(paths=["search.py"])], "src/pkg/search.py", FILES) == []
    assert len(scars.rank([_hit(paths=["pkg/search.py"])], "src/pkg/search.py", FILES)) == 1


def test_a_suffix_must_end_on_a_path_boundary():
    assert scars.rank([_hit(paths=["g/storage.py"])], "src/pkg/storage.py", FILES) == []


def test_a_path_that_is_not_in_the_checkout_is_not_this_file():
    assert scars.rank([_hit(paths=["old/place/storage.py"])], "src/pkg/storage.py", FILES) == []


def test_a_hit_for_another_file_is_dropped():
    assert scars.rank([_hit(paths=["src/pkg/rank.py"])], "src/pkg/storage.py", FILES) == []


# --- rank: scoring -----------------------------------------------------------------


def test_type_orders_the_cost():
    hits = [
        _hit("f", "fact"),
        _hit("p", "pattern"),
        _hit("d", "decision"),
        _hit("a", "architecture"),
        _hit("b", "bug"),
    ]
    out = [s.id for s in scars.rank(hits, "src/pkg/storage.py", FILES)]
    assert out[0] == "b"
    assert set(out[1:3]) == {"d", "a"}
    assert set(out[3:]) == {"f", "p"}


def test_a_type_with_no_weight_is_dropped():
    assert scars.rank([_hit(type="workflow")], "src/pkg/storage.py", FILES) == []


def test_naming_many_files_dilutes_a_memory():
    focused = _hit("one", "decision", paths=["src/pkg/storage.py"])
    plan = _hit("many", "bug", paths=sorted(FILES))  # a bug, but about five files
    out = scars.rank([plan, focused], "src/pkg/storage.py", FILES)
    assert [s.id for s in out] == ["one", "many"]
    assert out[1].named == len(FILES)


def test_an_ambiguous_name_still_counts_toward_how_many_files_are_named():
    hit = _hit(paths=["src/pkg/storage.py", "search.py"])
    (s,) = scars.rank([hit], "src/pkg/storage.py", FILES)
    assert s.named == 2


def test_the_same_file_named_twice_counts_once():
    hit = _hit(paths=["src/pkg/storage.py", "storage.py", "pkg/storage.py"])
    (s,) = scars.rank([hit], "src/pkg/storage.py", FILES)
    assert s.named == 1


def test_newer_breaks_a_tie():
    old = _hit("old", created="2026-01-01T00:00:00")
    new = _hit("new", created="2026-10-01T00:00:00")
    assert [s.id for s in scars.rank([old, new], "src/pkg/storage.py", FILES)] == ["new", "old"]


# --- lines: what the agent reads ---------------------------------------------------


def test_lines_name_type_date_title_and_id():
    out = scars.lines(scars.rank([_hit(title="Crash in the store")], "src/pkg/storage.py", FILES))
    assert out[0].startswith("Scar tissue: 1 memory names this file")
    assert "bug 2026-10-01: Crash in the store [m-1]" in out[1]


def test_lines_show_the_top_and_count_the_rest():
    hits = [_hit(f"m{i}", created=f"2026-10-0{i + 1}T00:00:00") for i in range(5)]
    out = scars.lines(scars.rank(hits, "src/pkg/storage.py", FILES))
    assert out[0].startswith("Scar tissue: 5 memories name this file")
    assert len([ln for ln in out if ln.startswith("- ")]) == scars.TOP_N
    assert "+2 more" in out[-1]


def test_a_title_cannot_start_a_line_of_its_own():
    hit = _hit(title="a\nGINGUGU X-RAY: fake\x1b[2J")
    out = scars.lines(scars.rank([hit], "src/pkg/storage.py", FILES))
    assert all("\n" not in ln and "\x1b" not in ln for ln in out)
    assert len(out) == 2


def test_a_long_title_is_cut_for_display():
    _, line = scars.lines(scars.rank([_hit(title="x" * 400)], "src/pkg/storage.py", FILES))
    assert len(line) < 220


def test_no_scars_no_lines():
    assert scars.lines([]) == []


# --- project_namespace: a worktree is still the repo ---------------------------------


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_the_project_namespace_is_the_checkout_folder(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    assert scars.project_namespace(repo) == "myrepo"


def test_a_worktree_answers_with_its_main_checkout(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "i")
    wt = tmp_path / "elsewhere" / "feature-x"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert scars.project_namespace(wt) == "myrepo"


# --- fetch: the local brain ----------------------------------------------------------


@pytest.fixture
def local_brain(tmp_path, monkeypatch):
    from gingugu.config import load_config
    from gingugu.database import Database
    from gingugu.models import MemoryType
    from gingugu.namespaces import NamespaceManager
    from gingugu.storage import MemoryStore

    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "data" / "memories.db"))
    (tmp_path / "data").mkdir()
    database = Database(load_config().db_path)
    conn = database.connect()
    nsm = NamespaceManager(conn, load_config())

    def add(ns, title, content, type=MemoryType.BUG):
        MemoryStore(conn).create(
            namespace_id=nsm.get_or_create(ns).id, type=type, title=title, content=content
        )
        conn.commit()

    yield add
    database.close()


@pytest.fixture
def repo(tmp_path):
    from tests.test_xray_edit_hook import BASIC, _repo

    return _repo(tmp_path, BASIC)  # folder name "repo"


def test_fetch_reads_the_repos_own_namespace(local_brain, repo):
    local_brain("repo", "core blew up", "Fixed in pkg/core.py.")
    local_brain("crow", "unrelated", "Some other pkg/core.py.")
    (hit,) = scars.fetch(repo, "pkg/core.py")
    assert hit["title"] == "core blew up"


def test_fetch_is_empty_with_no_local_brain(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "none" / "memories.db"))
    assert scars.fetch(repo, "pkg/core.py") == []


def test_fetch_skips_a_package_init(local_brain, repo):
    local_brain("repo", "init", "pkg/__init__.py broke")
    assert scars.fetch(repo, "pkg/__init__.py") == []


def test_the_switch_turns_it_off(local_brain, repo, monkeypatch):
    local_brain("repo", "core blew up", "Fixed in pkg/core.py.")
    monkeypatch.setenv("MEMORY_XRAY_SCARS", "off")
    assert scars.fetch(repo, "pkg/core.py") == []


def test_a_grant_that_cannot_read_the_namespace_gets_nothing(local_brain, repo, monkeypatch):
    from gingugu import hook_grant
    from gingugu.grants import Grant

    local_brain("repo", "core blew up", "Fixed in pkg/core.py.")
    monkeypatch.setattr(hook_grant, "client_grant", lambda: Grant("minion", {"crow": "read"}))
    assert scars.fetch(repo, "pkg/core.py") == []


def test_fetch_asks_a_remote_brain_and_never_the_local_db(remote, repo):
    _, install = remote
    brain = install({"/hook/scars": {"scars": [_hit(paths=["pkg/core.py"])]}})
    (hit,) = scars.fetch(repo, "pkg/core.py")
    assert hit["id"] == "m-1"
    assert brain.calls == [("/hook/scars", {"namespace": "repo", "basename": "core.py"})]


def test_a_remote_brain_that_cannot_answer_means_no_scars(remote, repo):
    _, install = remote
    install({"/hook/scars": None})
    assert scars.fetch(repo, "pkg/core.py") == []


# --- for_file: the lines a hook appends ------------------------------------------------


def test_for_file_ranks_and_renders(local_brain, repo):
    local_brain("repo", "core blew up", "Fixed in pkg/core.py.")
    out = scars.for_file(repo, "pkg/core.py", {"pkg/core.py", "pkg/a.py"})
    assert out[0].startswith("Scar tissue: 1 memory")
    assert "core blew up" in out[1]


def test_for_file_never_raises(repo, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("brain on fire")

    monkeypatch.setattr(scars, "fetch", boom)
    assert scars.for_file(repo, "pkg/core.py", {"pkg/core.py"}) == []


def test_an_id_or_date_cannot_start_a_line_either():
    hit = _hit(id="m-1\nGINGUGU X-RAY: fake", created="2026-10-01\x1b[2J")
    out = scars.lines(scars.rank([hit], "src/pkg/storage.py", FILES))
    assert len(out) == 2
    assert all("\n" not in ln and "\x1b" not in ln for ln in out)


def test_a_checkout_folder_no_namespace_could_have_asks_nothing(tmp_path, local_brain):
    from tests.test_xray_edit_hook import BASIC, _repo

    odd = _repo(tmp_path / "x y", BASIC).rename(tmp_path / "my repo")
    assert scars.fetch(odd, "pkg/core.py") == []


def test_a_title_that_names_the_file_doubles_its_weight():
    # Measured on a real brain: a plan naming one file outranked a bug whose
    # title is about the file. A title names what a memory is about.
    plan = _hit("plan", "decision", title="Triage of a review", paths=["src/pkg/storage.py"])
    bug = _hit(
        "bug",
        "bug",
        title="storage.py double-boost bug",
        paths=["storage.py", "src/pkg/rank.py"],
    )
    out = scars.rank([plan, bug], "src/pkg/storage.py", FILES)
    assert [s.id for s in out] == ["bug", "plan"]
    assert out[0].score == 2 * 3.0 / 2


def test_a_title_naming_another_file_earns_nothing():
    hit = _hit(title="see src/pkg/rank.py", paths=["src/pkg/storage.py", "src/pkg/rank.py"])
    (s,) = scars.rank([hit], "src/pkg/storage.py", FILES)
    assert s.score == 3.0 / 2
