"""Board row 1b: Python packages rooted somewhere other than the repo root or ``src/``.

A service whose code lives under ``api/`` runs with ``api/`` on ``sys.path``, so
``from controllers import console`` there means ``api/controllers/console.py``.
Before this, ``ModuleIndex`` named modules from the repo root and ``src/`` only,
and every such import counted as external: no import edge, no call edge, no
cross-file route mount (measured on dify: 5197 files, 1019 edges).

A directory is an inferred source root when it is at most two levels deep, is
not itself inside a package (no ``__init__.py`` in it or any ancestor), and
directly holds a regular package (a child directory with ``__init__.py``).
A file resolves against its own inferred root first, then the repo-wide index.
"""

from __future__ import annotations

from gingugu.xray.lang.python import ADAPTER
from gingugu.xray.resolve import ModuleIndex


def _extract(files: dict[str, str]):
    return ADAPTER.extract({k: v.encode() for k, v in files.items()}, {})


def _edges(out) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in out.edges}


def _calls(out) -> set[tuple[str, str, str, str]]:
    return set(out.symbols.edges)


API = {
    "api/app.py": "from controllers import console\nimport core.store\n",
    "api/controllers/__init__.py": "",
    "api/controllers/console.py": "from core.store import save\n\ndef go():\n    save()\n",
    "api/core/__init__.py": "",
    "api/core/store.py": "def save():\n    pass\n",
}


def test_absolute_imports_under_an_api_root_resolve():
    out = _extract(API)
    edges = _edges(out)
    assert ("api/app.py", "api/core/store.py") in edges
    assert ("api/app.py", "api/controllers/__init__.py") in edges
    assert ("api/app.py", "api/controllers/console.py") in edges  # candidate submodule
    assert ("api/controllers/console.py", "api/core/store.py") in edges
    assert out.external == 0


def test_a_call_through_an_inferred_root_is_a_call_edge():
    calls = _calls(_extract(API))
    assert any(
        c[0] == "api/controllers/console.py" and c[2] == "api/core/store.py" and c[3] == "save"
        for c in calls
    ), calls


def test_a_two_level_root_resolves():
    files = {
        "apps/backend/main.py": "import core.db\n",
        "apps/backend/core/__init__.py": "",
        "apps/backend/core/db.py": "",
    }
    assert ("apps/backend/main.py", "apps/backend/core/db.py") in _edges(_extract(files))


def test_a_three_level_directory_is_not_a_root():
    files = {
        "a/b/c/main.py": "import core.db\n",
        "a/b/c/core/__init__.py": "",
        "a/b/c/core/db.py": "",
    }
    out = _extract(files)
    assert ("a/b/c/main.py", "a/b/c/core/db.py") not in _edges(out)
    assert out.external == 1


def test_a_directory_inside_a_package_is_never_a_root():
    files = {
        "pkg/__init__.py": "",
        "pkg/sub/inner/__init__.py": "",
        "pkg/sub/inner/mod.py": "",
        "pkg/sub/user.py": "import inner.mod\n",
    }
    out = _extract(files)
    assert ("pkg/sub/user.py", "pkg/sub/inner/mod.py") not in _edges(out)
    assert out.external == 1


def test_a_directory_of_plain_modules_is_not_a_root():
    files = {"scripts/tool.py": "import helper\n", "scripts/helper.py": ""}
    out = _extract(files)
    assert ("scripts/tool.py", "scripts/helper.py") not in _edges(out)
    assert out.external == 1


def test_a_file_prefers_its_own_root_over_a_sibling_service():
    files = {
        "api/main.py": "import core.db\n",
        "api/core/__init__.py": "",
        "api/core/db.py": "",
        "worker/main.py": "import core.db\n",
        "worker/core/__init__.py": "",
        "worker/core/db.py": "",
    }
    edges = _edges(_extract(files))
    assert ("api/main.py", "api/core/db.py") in edges
    assert ("worker/main.py", "worker/core/db.py") in edges
    assert ("api/main.py", "worker/core/db.py") not in edges
    assert ("worker/main.py", "api/core/db.py") not in edges


def test_a_file_prefers_its_own_root_over_the_repo_root():
    files = {
        "core/__init__.py": "",
        "core/db.py": "",
        "api/main.py": "import core.db\n",
        "api/core/__init__.py": "",
        "api/core/db.py": "",
        "tool.py": "import core.db\n",
    }
    edges = _edges(_extract(files))
    assert ("api/main.py", "api/core/db.py") in edges
    assert ("tool.py", "core/db.py") in edges  # outside every inferred root: unchanged


def test_the_repo_root_and_src_still_win_for_files_outside_inferred_roots():
    idx = ModuleIndex(["src/core/__init__.py", "api/core/__init__.py", "api/core/x.py"])
    assert idx.names["core"] == "src/core/__init__.py"


def test_the_repo_root_spelling_of_a_module_still_resolves():
    files = {
        "api/main.py": "",
        "api/core/__init__.py": "",
        "api/core/db.py": "",
        "tests/test_db.py": "import api.core.db\n",
    }
    assert ("tests/test_db.py", "api/core/db.py") in _edges(_extract(files))


def test_relative_imports_under_an_inferred_root_are_unchanged():
    files = {
        "api/core/__init__.py": "",
        "api/core/a.py": "from . import b\nfrom .b import f\n",
        "api/core/b.py": "def f():\n    pass\n",
    }
    assert ("api/core/a.py", "api/core/b.py") in _edges(_extract(files))


def test_a_third_party_import_stays_external():
    out = _extract({**API, "api/net.py": "import requests\nfrom flask import Flask\n"})
    assert out.external == 2


def test_a_layout_with_no_inferred_root_is_byte_identical_to_before():
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/a.py": "from pkg import b\nimport os\n",
        "src/pkg/b.py": "",
        "tests/test_a.py": "import pkg.a\n",
    }
    idx = ModuleIndex(list(files))
    assert idx.view("tests/test_a.py") is idx
    assert idx.view("src/pkg/a.py") is idx
    out = _extract(files)
    assert ("tests/test_a.py", "src/pkg/a.py") in _edges(out)
    assert out.external == 1


def test_inferred_roots_are_deterministic():
    paths = ["b/core/__init__.py", "a/core/__init__.py", "x/y/core/__init__.py"]
    assert ModuleIndex(paths).roots == ModuleIndex(list(reversed(paths))).roots
    assert ModuleIndex(paths).roots == ("src/", "", "a/", "b/", "x/y/")


def test_views_are_built_once_per_root():
    idx = ModuleIndex(list(API))
    assert idx.view("api/app.py") is idx.view("api/core/store.py")
