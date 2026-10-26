"""X-ray extraction: a git working tree -> files, import edges, a content fingerprint.

Runs on the CLIENT (the proxy or the local stdio server), never on the brain:
it reads the caller's disk. Nothing here imports or executes repo code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from gingugu.xray import extract
from gingugu.xray.extract import XrayError


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path, files: dict[str, str], *, commit: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    if commit:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
    return repo


def _edges(x) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in x.edges}


def _edge(x, src: str, dst: str):
    found = [e for e in x.edges if e.src == src and e.dst == dst]
    assert len(found) == 1, f"{src}->{dst}: {found}"
    return found[0]


# --- files --------------------------------------------------------------------


def test_lists_tracked_and_untracked_python_but_not_ignored(tmp_path):
    repo = _repo(tmp_path, {"a.py": "", ".gitignore": "ignored.py\n", "notes.txt": "x"})
    (repo / "new.py").write_text("")
    (repo / "ignored.py").write_text("")
    x = extract.extract(repo)
    assert sorted(f.path for f in x.files) == ["a.py", "new.py"]


def test_a_deleted_tracked_file_is_gone(tmp_path):
    repo = _repo(tmp_path, {"a.py": "", "b.py": "import a\n"})
    (repo / "a.py").unlink()
    x = extract.extract(repo)
    assert [f.path for f in x.files] == ["b.py"]
    assert _edges(x) == set()


def test_a_subdirectory_path_resolves_to_the_repo_root(tmp_path):
    repo = _repo(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": ""})
    x = extract.extract(repo / "pkg")
    assert x.root == str(repo.resolve())
    assert {f.path for f in x.files} == {"pkg/__init__.py", "pkg/a.py"}


def test_not_a_git_repo_is_refused(tmp_path):
    (tmp_path / "a.py").write_text("")
    with pytest.raises(XrayError):
        extract.extract(tmp_path)


def test_commit_is_head_and_none_when_unborn(tmp_path):
    repo = _repo(tmp_path, {"a.py": ""})
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert extract.extract(repo).commit == head

    unborn = _repo(tmp_path / "u", {"a.py": ""}, commit=False)
    x = extract.extract(unborn)
    assert x.commit is None
    assert [f.path for f in x.files] == ["a.py"]


# --- resolution -----------------------------------------------------------------


def test_absolute_import_resolves_and_third_party_is_external(tmp_path):
    repo = _repo(tmp_path, {"a.py": "import b\nimport os\nimport requests\n", "b.py": ""})
    x = extract.extract(repo)
    assert _edges(x) == {("a.py", "b.py")}
    assert x.external == 2
    assert x.unresolved == 0


def test_src_layout_resolves(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/core.py": "",
            "tests/test_core.py": "from pkg import core\n",
        },
    )
    x = extract.extract(repo)
    assert ("tests/test_core.py", "src/pkg/core.py") in _edges(x)
    assert ("tests/test_core.py", "src/pkg/__init__.py") in _edges(x)


def test_dotted_import_depends_on_every_package_initializer(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "a/__init__.py": "",
            "a/b/__init__.py": "",
            "a/b/c.py": "",
            "main.py": "import a.b.c\n",
        },
    )
    x = extract.extract(repo)
    assert {d for s, d in _edges(x) if s == "main.py"} == {
        "a/__init__.py",
        "a/b/__init__.py",
        "a/b/c.py",
    }
    assert all(e.certain for e in x.edges if e.src == "main.py")
    # The parents run because the target does; only the target was asked for.
    assert {e.dst: e.implicit for e in x.edges if e.src == "main.py"} == {
        "a/__init__.py": True,
        "a/b/__init__.py": True,
        "a/b/c.py": False,
    }


def test_implicit_marks_parent_initializers_of_from_imports(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/mod.py": "",
            "main.py": "from pkg.sub import mod\nfrom pkg import sub\n",
        },
    )
    x = extract.extract(repo)
    # Both statements ask only for submodules, so their packages are containers:
    # they run, nobody asked for them.
    assert _edge(x, "main.py", "pkg/__init__.py").implicit is True
    assert _edge(x, "main.py", "pkg/sub/mod.py").implicit is False


def test_from_import_of_a_name_makes_the_package_explicit(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "pkg/__init__.py": "VALUE = 1\n",
            "pkg/mod.py": "",
            "a.py": "from pkg import mod\n",
            "b.py": "from pkg import mod, VALUE\n",
        },
    )
    x = extract.extract(repo)
    assert _edge(x, "a.py", "pkg/__init__.py").implicit is True  # only a submodule asked for
    assert _edge(x, "b.py", "pkg/__init__.py").implicit is False  # VALUE lives in pkg


def test_an_implicit_parent_alone_stays_implicit(tmp_path):
    repo = _repo(
        tmp_path, {"pkg/__init__.py": "", "pkg/mod.py": "", "main.py": "from pkg.mod import f\n"}
    )
    x = extract.extract(repo)
    assert _edge(x, "main.py", "pkg/__init__.py").implicit is True
    assert _edge(x, "main.py", "pkg/mod.py").implicit is False


def test_namespace_package_without_init_resolves(tmp_path):
    repo = _repo(tmp_path, {"ns/mod.py": "", "main.py": "import ns.mod\n"})
    x = extract.extract(repo)
    assert _edges(x) == {("main.py", "ns/mod.py")}


def test_from_import_of_a_submodule_is_a_candidate_edge(tmp_path):
    repo = _repo(
        tmp_path,
        {"pkg/__init__.py": "", "pkg/mod.py": "", "main.py": "from pkg import mod, helper\n"},
    )
    x = extract.extract(repo)
    assert _edge(x, "main.py", "pkg/__init__.py").certain is True
    assert _edge(x, "main.py", "pkg/mod.py").certain is False
    # `helper` is not a file. It may be a name the package defines or nothing at all;
    # static reading can't tell, so by policy it gets no edge and is not unresolved.
    assert _edges(x) == {("main.py", "pkg/__init__.py"), ("main.py", "pkg/mod.py")}
    assert x.unresolved == 0


def test_from_import_of_a_module_is_certain(tmp_path):
    repo = _repo(
        tmp_path, {"pkg/__init__.py": "", "pkg/mod.py": "", "main.py": "from pkg.mod import f\n"}
    )
    x = extract.extract(repo)
    assert _edge(x, "main.py", "pkg/mod.py").certain is True
    assert _edge(x, "main.py", "pkg/__init__.py").certain is True


def test_relative_imports_from_a_module_and_from_an_init(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "pkg/__init__.py": "from .a import x\n",
            "pkg/a.py": "from . import b\nfrom .sub.c import y\n",
            "pkg/b.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/c.py": "from ..b import z\n",
        },
    )
    x = extract.extract(repo)
    edges = _edges(x)
    assert ("pkg/__init__.py", "pkg/a.py") in edges
    assert ("pkg/a.py", "pkg/b.py") in edges
    assert ("pkg/a.py", "pkg/sub/c.py") in edges
    assert ("pkg/sub/c.py", "pkg/b.py") in edges


def test_unresolved_and_dynamic_imports_are_counted(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": (
                "from . import maybe_a_name\n"  # a name in pkg/__init__: not unresolved
                "from .gone import x\n"
                "import pkg.gone2\n"
                "import importlib\n"
                "importlib.import_module('pkg.x')\n"
                "__import__('pkg.y')\n"
            ),
        },
    )
    x = extract.extract(repo)
    assert x.unresolved == 2  # .gone, pkg.gone2
    assert x.dynamic == 2
    # What does exist still counts: both imports run pkg/__init__.
    assert _edges(x) == {("pkg/a.py", "pkg/__init__.py")}


def test_self_imports_are_dropped_and_edges_deduplicated(tmp_path):
    repo = _repo(tmp_path, {"a.py": "import a\nimport b\nimport b\nfrom b import f\n", "b.py": ""})
    x = extract.extract(repo)
    assert [(e.src, e.dst) for e in x.edges] == [("a.py", "b.py")]


# --- edge kinds -------------------------------------------------------------------


def test_kinds_classify_type_checking_conditional_and_lazy(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "m.py": "",
            "t.py": "",
            "c.py": "",
            "l.py": "",
            "main.py": (
                "from typing import TYPE_CHECKING\n"
                "import m\n"
                "if TYPE_CHECKING:\n"
                "    import t\n"
                "try:\n"
                "    import c\n"
                "except ImportError:\n"
                "    pass\n"
                "def f():\n"
                "    import l\n"
            ),
        },
    )
    x = extract.extract(repo)
    assert _edge(x, "main.py", "m.py").kind == "module"
    assert _edge(x, "main.py", "t.py").kind == "type_checking"
    assert _edge(x, "main.py", "c.py").kind == "conditional"
    assert _edge(x, "main.py", "l.py").kind == "lazy"


def test_typing_dot_type_checking_is_recognized(tmp_path):
    repo = _repo(
        tmp_path, {"t.py": "", "main.py": "import typing\nif typing.TYPE_CHECKING:\n    import t\n"}
    )
    assert _edge(extract.extract(repo), "main.py", "t.py").kind == "type_checking"


def test_a_pair_imported_twice_keeps_one_real_occurrence(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/mod.py": "",
            "main.py": "def f():\n    import pkg.mod\nfrom pkg import mod\n",
        },
    )
    e = _edge(extract.extract(repo), "main.py", "pkg/mod.py")
    # lazy+certain and module+candidate: the edge is the strongest single occurrence
    # (certain first, then most binding kind), never fields mixed from two.
    assert (e.certain, e.kind) == (True, "lazy")


def test_merge_prefers_the_most_binding_kind_among_certain_occurrences(tmp_path):
    repo = _repo(tmp_path, {"b.py": "", "main.py": "def f():\n    import b\nimport b\n"})
    e = _edge(extract.extract(repo), "main.py", "b.py")
    assert (e.certain, e.kind) == (True, "module")


def test_type_checking_wins_over_lazy(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "t.py": "",
            "main.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    def f():\n"
                "        import t\n"
            ),
        },
    )
    assert _edge(extract.extract(repo), "main.py", "t.py").kind == "type_checking"


def test_tracked_symlinks_are_skipped(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("import os\n")
    repo = _repo(tmp_path, {"a.py": ""}, commit=False)
    (repo / "link.py").symlink_to(outside)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    x = extract.extract(repo)
    assert [f.path for f in x.files] == ["a.py"]
    assert x.skipped == [("link.py", "symlink")]


# --- coverage -------------------------------------------------------------------


def test_a_file_that_fails_to_parse_is_listed_and_has_no_edges(tmp_path):
    repo = _repo(tmp_path, {"bad.py": "import b\ndef (:\n", "b.py": ""})
    x = extract.extract(repo)
    assert x.parse_errors == ["bad.py"]
    assert "bad.py" in {f.path for f in x.files}
    assert _edges(x) == set()


# --- roots: tests and entry points ------------------------------------------------


def test_test_files_are_marked(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "tests/helpers.py": "",
            "test_top.py": "",
            "pkg/thing_test.py": "",
            "conftest.py": "",
            "pkg/core.py": "",
        },
    )
    marked = {f.path for f in extract.extract(repo).files if f.is_test}
    assert marked == {"tests/helpers.py", "test_top.py", "pkg/thing_test.py", "conftest.py"}


def test_entry_points_from_pyproject_main_guard_and_dunder_main(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname="x"\n[project.scripts]\nx = "src_pkg.cli:main"\n',
            "src/src_pkg/__init__.py": "",
            "src/src_pkg/cli.py": "",
            "src/src_pkg/__main__.py": "",
            "tool.py": "if __name__ == '__main__':\n    pass\n",
            "lib.py": "",
        },
    )
    marked = {f.path for f in extract.extract(repo).files if f.is_entry}
    assert marked == {"src/src_pkg/cli.py", "src/src_pkg/__main__.py", "tool.py"}


def test_gui_scripts_and_plugin_entry_points_are_entries(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname="x"\n'
                '[project.gui-scripts]\ng = "app.gui:run"\n'
                '[project.entry-points."pytest11"]\np = "app.plugin"\n'
            ),
            "app/__init__.py": "",
            "app/gui.py": "",
            "app/plugin.py": "",
            "app/lib.py": "",
        },
    )
    marked = {f.path for f in extract.extract(repo).files if f.is_entry}
    assert marked == {"app/gui.py", "app/plugin.py"}


def test_a_nested_main_guard_is_not_an_entry(tmp_path):
    repo = _repo(tmp_path, {"lib.py": "def f():\n    if __name__ == '__main__':\n        pass\n"})
    assert not any(f.is_entry for f in extract.extract(repo).files)


# --- fingerprint ----------------------------------------------------------------


def test_fingerprint_is_stable_and_matches_extract(tmp_path):
    repo = _repo(tmp_path, {"a.py": "import b\n", "b.py": ""})
    fp = extract.fingerprint(repo)
    assert fp == extract.fingerprint(repo)
    assert fp == extract.extract(repo).fingerprint


def test_fingerprint_changes_on_every_edit_not_just_status(tmp_path):
    repo = _repo(tmp_path, {"a.py": "x = 1\n"})
    clean = extract.fingerprint(repo)
    (repo / "a.py").write_text("x = 2\n")
    first = extract.fingerprint(repo)
    (repo / "a.py").write_text("x = 3\n")  # git status still says " M"
    second = extract.fingerprint(repo)
    assert len({clean, first, second}) == 3


def test_fingerprint_changes_on_add_delete_and_pyproject(tmp_path):
    repo = _repo(tmp_path, {"a.py": "", "pyproject.toml": "[project]\nname='x'\n"})
    seen = {extract.fingerprint(repo)}
    (repo / "b.py").write_text("")
    seen.add(extract.fingerprint(repo))
    (repo / "a.py").unlink()
    seen.add(extract.fingerprint(repo))
    (repo / "pyproject.toml").write_text("[project]\nname='y'\n")
    seen.add(extract.fingerprint(repo))
    assert len(seen) == 4


def test_fingerprint_is_content_addressed_not_commit_addressed(tmp_path):
    repo = _repo(tmp_path, {"a.py": "x = 1\n"})
    before = extract.fingerprint(repo)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
    assert extract.fingerprint(repo) == before


def test_fingerprint_includes_the_extractor_version(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {"a.py": ""})
    before = extract.fingerprint(repo)
    monkeypatch.setattr(extract, "EXTRACTOR_VERSION", extract.EXTRACTOR_VERSION + 1)
    assert extract.fingerprint(repo) != before


# --- wire shape ---------------------------------------------------------------------


def test_payload_is_plain_json_without_the_local_root(tmp_path):
    import json

    repo = _repo(tmp_path, {"a.py": "import b\n", "b.py": "", "tests/test_a.py": "import a\n"})
    x = extract.extract(repo)
    body = x.payload()
    json.dumps(body)  # serializable
    assert str(repo) not in json.dumps(body)  # never ships a client path to the brain
    assert body["fingerprint"] == x.fingerprint
    assert body["extractor_version"] == extract.EXTRACTOR_VERSION
    assert {"path": "tests/test_a.py", "is_test": True, "is_entry": False} in body["files"]
    assert {
        "src": "a.py",
        "dst": "b.py",
        "kind": "module",
        "certain": True,
        "implicit": False,
    } in body["edges"]
    assert set(body) >= {
        "commit",
        "unresolved",
        "dynamic",
        "external",
        "parse_errors",
        "skipped",
    }


# --- skipped sources, with a reason -----------------------------------------------


_POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")


@_POSIX
def test_an_unreadable_file_is_skipped_and_named(tmp_path):
    import os

    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    repo = _repo(tmp_path, {"a.py": "", "b.py": "import a\n"})
    (repo / "b.py").chmod(0)
    try:
        x = extract.extract(repo)
    finally:
        (repo / "b.py").chmod(0o644)
    assert [f.path for f in x.files] == ["a.py"]
    assert x.skipped == [("b.py", "unreadable")]


def test_a_file_reached_through_a_symlinked_directory_is_outside(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "b.py").write_text("")
    repo = _repo(tmp_path, {"a/b.py": "", "c.py": ""})
    import shutil

    shutil.rmtree(repo / "a")
    (repo / "a").symlink_to(elsewhere, target_is_directory=True)
    x = extract.extract(repo)
    assert [f.path for f in x.files] == ["c.py"]
    assert x.skipped == [("a/b.py", "outside")]


def test_a_file_over_the_size_cap_is_skipped(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {"big.py": "x = 1\n" * 100, "a.py": ""})
    monkeypatch.setattr(extract, "MAX_FILE_BYTES", 50)
    x = extract.extract(repo)
    assert [f.path for f in x.files] == ["a.py"]
    assert x.skipped == [("big.py", "too_large")]


def test_a_symlinked_pyproject_is_not_followed(tmp_path):
    outside = tmp_path / "pyproject.toml"
    outside.write_text('[project]\nname="x"\n[project.scripts]\nx = "a:main"\n')
    repo = _repo(tmp_path, {"a.py": ""})
    (repo / "pyproject.toml").symlink_to(outside)
    x = extract.extract(repo)
    assert not any(f.is_entry for f in x.files)
    assert ("pyproject.toml", "symlink") in x.skipped


def test_a_malformed_pyproject_is_named_not_silent(tmp_path):
    repo = _repo(tmp_path, {"a.py": "", "pyproject.toml": "[project\nbroken"})
    x = extract.extract(repo)
    assert ("pyproject.toml", "malformed") in x.skipped


def test_a_deleted_tracked_file_is_not_a_skip(tmp_path):
    repo = _repo(tmp_path, {"a.py": "", "b.py": ""})
    (repo / "b.py").unlink()
    assert extract.extract(repo).skipped == []


@_POSIX
def test_skips_change_the_fingerprint(tmp_path):
    repo = _repo(tmp_path, {"a.py": "", "b.py": ""})
    before = extract.fingerprint(repo)
    (repo / "b.py").chmod(0)
    try:
        assert extract.fingerprint(repo) != before
    finally:
        (repo / "b.py").chmod(0o644)


def test_payload_carries_skips_with_reasons(tmp_path):
    repo = _repo(tmp_path, {"a.py": ""}, commit=False)
    (repo / "link.py").symlink_to(repo / "a.py")
    body = extract.extract(repo).payload()
    assert body["skipped"] == [{"path": "link.py", "reason": "symlink"}]


# --- relative and namespace edge cases ---------------------------------------------


def test_a_relative_import_beyond_the_top_package_is_unresolved(tmp_path):
    repo = _repo(
        tmp_path, {"pkg/__init__.py": "", "pkg/a.py": "from ..other import x\n", "other.py": ""}
    )
    x = extract.extract(repo)
    assert ("pkg/a.py", "other.py") not in _edges(x)
    assert x.unresolved == 1


def test_a_relative_import_from_a_top_level_module_is_unresolved(tmp_path):
    repo = _repo(tmp_path, {"a.py": "from . import b\n", "b.py": ""})
    x = extract.extract(repo)
    assert _edges(x) == set()
    assert x.unresolved == 1


def test_namespace_package_imports_are_not_unresolved(tmp_path):
    repo = _repo(
        tmp_path,
        {"ns/mod.py": "", "a.py": "from ns import mod\n", "b.py": "import ns.mod\n"},
    )
    x = extract.extract(repo)
    assert ("a.py", "ns/mod.py") in _edges(x)
    assert ("b.py", "ns/mod.py") in _edges(x)
    assert x.unresolved == 0


# --- git hygiene -----------------------------------------------------------------


def test_repo_git_config_cannot_run_commands(tmp_path):
    marker = tmp_path / "ran"
    repo = _repo(tmp_path, {"a.py": ""})
    hook = repo / "fsmon.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(hook))
    extract.extract(repo)
    extract.fingerprint(repo)
    assert not marker.exists()
