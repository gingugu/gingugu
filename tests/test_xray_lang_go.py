"""The Go adapter: tree-sitter parse, ``go.mod`` resolution, package nodes.

A Go import names a package - a directory - not a file. Each package is a node
named by its import path (``example.com/m/internal/db``): an importer points at
the package (``module``, certain), the package points at each of its non-test
files (``member``, certain), and a file leans on its own package's siblings
(``package``, uncertain and implicit: reached by a change, never ranked). A
test file is not a member; it leans on its package (``package``, certain).

The adapter gets bytes only and never runs ``go``.
"""

from __future__ import annotations

import pytest

from gingugu.xray.lang.golang import ADAPTER
from tests.xray_lang_fixtures import edge, edges, node, run_adapter

MOD = {"go.mod": "module example.com/m\n\ngo 1.22\n"}


def run(sources: dict[str, str], manifests: dict[str, str] | None = None):
    return run_adapter(ADAPTER, sources, MOD if manifests is None else manifests)


def go(pkg: str, *imports: str) -> str:
    body = "".join(f'\t"{i}"\n' for i in imports)
    return f"package {pkg}\n\nimport (\n{body})\n" if imports else f"package {pkg}\n"


def test_claims_go_files_and_go_mod():
    assert ADAPTER.extensions == (".go",)
    assert ADAPTER.manifests == ("go.mod",)


# --- package nodes ----------------------------------------------------------------


def test_each_directory_is_a_package_node_named_by_import_path():
    res = run({"main.go": go("main"), "internal/db/db.go": go("db")})
    root, db = node(res, "example.com/m"), node(res, "example.com/m/internal/db")
    assert (root.kind, root.lang, root.is_test, root.is_entry) == ("package", "go", False, False)
    assert db.kind == "package"
    assert node(res, "internal/db/db.go").kind == "file"


def test_an_import_points_at_the_package_and_the_package_at_its_files():
    res = run(
        {
            "main.go": go("main", "example.com/m/internal/db"),
            "internal/db/db.go": go("db"),
            "internal/db/conn.go": go("db"),
        }
    )
    imp = edge(res, "main.go", "example.com/m/internal/db")
    assert (imp.kind, imp.certain, imp.implicit) == ("module", True, False)
    for f in ("internal/db/db.go", "internal/db/conn.go"):
        m = edge(res, "example.com/m/internal/db", f)
        assert (m.kind, m.certain, m.implicit) == ("member", True, False)


def test_siblings_lean_on_their_package_uncertain_and_implicit():
    res = run({"p/a.go": go("p"), "p/b.go": go("p")})
    for f in ("p/a.go", "p/b.go"):
        s = edge(res, f, "example.com/m/p")
        assert (s.kind, s.certain, s.implicit) == ("package", False, True)


def test_a_lone_file_has_no_sibling_edge():
    res = run({"p/a.go": go("p")})
    assert edges(res) == {("example.com/m/p", "p/a.go")}


def test_a_test_file_is_a_test_leaning_on_its_package_not_a_member():
    res = run(
        {
            "p/a.go": go("p"),
            "p/a_test.go": go("p"),
            "p/ext_test.go": go("p_test", "example.com/m/p"),
        }
    )
    assert node(res, "p/a_test.go").is_test is True
    assert ("example.com/m/p", "p/a_test.go") not in edges(res)
    for t in ("p/a_test.go", "p/ext_test.go"):
        e = edge(res, t, "example.com/m/p")
        assert (e.kind, e.certain, e.implicit) == ("package", True, False)


def test_a_directory_of_only_tests_has_no_package_node():
    res = run({"e2e/x_test.go": go("e2e")})
    assert [f.path for f in res.files] == ["e2e/x_test.go"]
    assert res.edges == []


# --- resolution -----------------------------------------------------------------------


def test_stdlib_cgo_and_third_party_are_external():
    src = go("main", "fmt", "net/http", "C", "github.com/spf13/cobra", "golang.org/x/sync/errgroup")
    res = run({"main.go": src})
    assert res.external == 5
    assert all(e.dst != "fmt" for e in res.edges)


def test_an_in_module_path_with_no_package_is_unresolved():
    res = run({"main.go": go("main", "example.com/m/gone")})
    assert res.unresolved == 1


def test_a_module_whose_name_has_no_dot_still_resolves():
    res = run(
        {"main.go": go("main", "mytool/util"), "util/u.go": go("util")},
        {"go.mod": "module mytool\n"},
    )
    assert ("main.go", "mytool/util") in edges(res)
    assert res.external == 0


def test_a_prefix_lookalike_is_not_the_module():
    res = run({"main.go": go("main", "example.com/mother/x")})
    assert res.external == 1


def test_import_forms_alias_dot_blank_and_single_line():
    src = (
        "package main\n\n"
        'import u "example.com/m/u"\n'
        'import . "example.com/m/d"\n'
        'import _ "example.com/m/b"\n'
    )
    res = run({"main.go": src, "u/u.go": go("u"), "d/d.go": go("d"), "b/b.go": go("b")})
    assert {d for s, d in edges(res) if s == "main.go"} == {
        "example.com/m/u",
        "example.com/m/d",
        "example.com/m/b",
    }


def test_a_raw_string_import_path():
    res = run({"main.go": "package main\nimport `example.com/m/u`\n", "u/u.go": go("u")})
    assert ("main.go", "example.com/m/u") in edges(res)


def test_nested_modules_own_their_directories_and_import_each_other():
    res = run(
        {
            "main.go": go("main", "example.com/tools/lint"),
            "tools/lint/l.go": go("lint", "example.com/m/core"),
            "core/c.go": go("core"),
        },
        {"go.mod": "module example.com/m\n", "tools/go.mod": "module example.com/tools\n"},
    )
    assert node(res, "example.com/tools/lint").kind == "package"
    assert ("main.go", "example.com/tools/lint") in edges(res)
    assert ("tools/lint/l.go", "example.com/m/core") in edges(res)
    assert all(f.path != "example.com/m/tools/lint" for f in res.files)


def test_go_mod_comments_and_quoted_module_path():
    res = run(
        {"main.go": go("main", "example.com/q/u"), "u/u.go": go("u")},
        {"go.mod": '// header\nmodule "example.com/q" // trailing\n'},
    )
    assert ("main.go", "example.com/q/u") in edges(res)


def test_files_outside_any_module_are_nodes_without_packages():
    res = run({"loose/a.go": go("a", "example.com/x")}, {})
    assert [f.path for f in res.files] == ["loose/a.go"]
    assert res.edges == []
    assert res.external == 1


def test_a_go_mod_without_a_module_line_is_malformed():
    res = run({"a.go": go("a")}, {"go.mod": "go 1.22\n"})
    assert ("go.mod", "malformed") in res.skipped
    assert [f.path for f in res.files] == ["a.go"]


# --- entries, parse errors -------------------------------------------------------------


def test_package_main_files_are_entries():
    res = run(
        {"cmd/app/main.go": go("main"), "cmd/app/flags.go": go("main"), "lib/l.go": go("lib")}
    )
    assert {f.path for f in res.files if f.is_entry} == {"cmd/app/main.go", "cmd/app/flags.go"}


def test_a_syntax_error_is_reported_and_the_file_stays_a_member():
    res = run({"p/a.go": "package p\nfunc {\n"})
    assert res.parse_errors == ["p/a.go"]
    assert ("example.com/m/p", "p/a.go") in edges(res)


@pytest.mark.parametrize("n", [1, 3])
def test_edges_are_unique(n):
    imports = ["example.com/m/u"] * n
    res = run({"a/a.go": go("a", *imports), "u/u.go": go("u")})
    assert [e for e in res.edges if e.src == "a/a.go"] == [edge(res, "a/a.go", "example.com/m/u")]
