"""The TypeScript/JavaScript adapter's config-driven resolution: tsconfig/jsconfig,
in-repo workspace packages (``package.json``), and entry points.

Configs are read as data - JSONC parsed in Python, ``extends`` followed only to
files in the repo - and nothing is ever run.
"""

from __future__ import annotations

from gingugu.xray.lang.typescript import ADAPTER
from tests.xray_lang_fixtures import edge, edges, node, run_adapter


def run(sources: dict[str, str], manifests: dict[str, str] | None = None):
    return run_adapter(ADAPTER, sources, manifests)


# --- tsconfig ------------------------------------------------------------------------


def test_tsconfig_paths_with_base_url():
    tsconfig = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
    res = run(
        {"src/app.ts": "import x from '@/lib/x'\n", "src/lib/x.ts": ""},
        {"tsconfig.json": tsconfig},
    )
    assert edges(res) == {("src/app.ts", "src/lib/x.ts")}
    assert res.external == 0


def test_tsconfig_paths_without_base_url_are_relative_to_the_config():
    tsconfig = '{"compilerOptions": {"paths": {"~lib/*": ["./lib/*"]}}}'
    res = run(
        {"web/app.ts": "import x from '~lib/x'\n", "web/lib/x.ts": ""},
        {"web/tsconfig.json": tsconfig},
    )
    assert edges(res) == {("web/app.ts", "web/lib/x.ts")}


def test_paths_fall_through_their_targets_in_order():
    tsconfig = '{"compilerOptions": {"paths": {"@/*": ["./gen/*", "./src/*"]}}}'
    res = run({"app.ts": "import x from '@/x'\n", "src/x.ts": ""}, {"tsconfig.json": tsconfig})
    assert edges(res) == {("app.ts", "src/x.ts")}


def test_an_exact_paths_key_without_a_star():
    tsconfig = '{"compilerOptions": {"paths": {"config": ["./src/config.ts"]}}}'
    res = run(
        {"app.ts": "import c from 'config'\n", "src/config.ts": ""}, {"tsconfig.json": tsconfig}
    )
    assert edges(res) == {("app.ts", "src/config.ts")}


def test_base_url_alone_resolves_bare_specifiers_that_exist():
    tsconfig = '{"compilerOptions": {"baseUrl": "src"}}'
    res = run(
        {"src/app.ts": "import u from 'utils/u'\nimport r from 'react'\n", "src/utils/u.ts": ""},
        {"tsconfig.json": tsconfig},
    )
    assert edges(res) == {("src/app.ts", "src/utils/u.ts")}
    assert res.external == 1


def test_the_nearest_tsconfig_governs_a_file():
    root = '{"compilerOptions": {"paths": {"@/*": ["./root/*"]}}}'
    inner = '{"compilerOptions": {"paths": {"@/*": ["./inner/*"]}}}'
    res = run(
        {"pkg/app.ts": "import x from '@/x'\n", "pkg/inner/x.ts": "", "root/x.ts": ""},
        {"tsconfig.json": root, "pkg/tsconfig.json": inner},
    )
    assert edges(res) == {("pkg/app.ts", "pkg/inner/x.ts")}


def test_jsconfig_counts_like_tsconfig():
    jsconfig = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
    res = run({"app.js": "import x from '@/x'\n", "src/x.js": ""}, {"jsconfig.json": jsconfig})
    assert edges(res) == {("app.js", "src/x.js")}


def test_tsconfig_is_jsonc_comments_and_trailing_commas():
    tsconfig = """{
      // line comment
      "compilerOptions": {
        /* block */ "baseUrl": ".",
        "paths": { "@/*": ["src/*",], },
      },
    }"""
    res = run({"a.ts": "import x from '@/x'\n", "src/x.ts": ""}, {"tsconfig.json": tsconfig})
    assert edges(res) == {("a.ts", "src/x.ts")}


def test_a_comment_marker_inside_a_string_is_not_a_comment():
    tsconfig = '{"compilerOptions": {"paths": {"@//*": ["./src/*"]}}}'
    res = run({"a.ts": "import x from '@//x'\n", "src/x.ts": ""}, {"tsconfig.json": tsconfig})
    assert edges(res) == {("a.ts", "src/x.ts")}


def test_relative_extends_is_followed_and_base_url_stays_with_its_config():
    base = '{"compilerOptions": {"baseUrl": "./src", "paths": {"@/*": ["*"]}}}'
    child = '{"extends": "../tsconfig.base.json", "compilerOptions": {}}'
    res = run(
        {"app/main.ts": "import x from '@/x'\n", "src/x.ts": ""},
        {"tsconfig.base.json": base, "app/tsconfig.json": child},
    )
    assert edges(res) == {("app/main.ts", "src/x.ts")}


def test_an_extends_array_and_a_package_extends_are_tolerated():
    child = (
        '{"extends": ["@tsconfig/node20/tsconfig.json", "./tsconfig.b.json"],'
        ' "compilerOptions": {}}'
    )
    b = '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}'
    res = run(
        {"a.ts": "import x from '@/x'\n", "src/x.ts": ""},
        {"tsconfig.json": child, "tsconfig.b.json": b},
    )
    assert edges(res) == {("a.ts", "src/x.ts")}


def test_an_extends_cycle_terminates():
    a = '{"extends": "./tsconfig.b.json", "compilerOptions": {}}'
    b = '{"extends": "./tsconfig.json"}'
    res = run({"a.ts": "import x from 'x'\n"}, {"tsconfig.json": a, "tsconfig.b.json": b})
    assert res.external == 1


def test_a_malformed_tsconfig_is_skipped_and_resolution_continues():
    res = run({"a.ts": "import x from './x'\n", "x.ts": ""}, {"tsconfig.json": "{nope"})
    assert edges(res) == {("a.ts", "x.ts")}
    assert ("tsconfig.json", "malformed") in res.skipped


# --- workspace packages (package.json "name") -------------------------------------


def test_a_workspace_package_name_resolves_to_its_entry_field():
    pkg = '{"name": "@acme/util", "main": "./lib/main.js", "module": "./lib/esm.ts"}'
    res = run(
        {"apps/web/a.ts": "import u from '@acme/util'\n", "packages/util/lib/esm.ts": ""},
        {"packages/util/package.json": pkg},
    )
    # "exports" > "module" > "main"; the first that names a source file wins.
    assert edges(res) == {("apps/web/a.ts", "packages/util/lib/esm.ts")}
    assert edge(res, "apps/web/a.ts", "packages/util/lib/esm.ts").certain is True
    assert res.external == 0


def test_exports_dot_beats_module_and_main_and_may_be_nested():
    pkg = (
        '{"name": "u", "main": "./m.js",'
        ' "exports": {".": {"types": "./t.d.ts", "import": "./src/i.ts"}}}'
    )
    res = run(
        {"a.ts": "import u from 'u'\n", "p/src/i.ts": "", "p/m.js": "", "p/t.d.ts": ""},
        {"p/package.json": pkg},
    )
    # In a nested condition map the first leaf naming a source file wins, in order.
    assert edges(res) == {("a.ts", "p/t.d.ts")}


def test_a_built_entry_falls_back_to_the_source_index_as_a_candidate():
    pkg = '{"name": "u", "main": "./dist/index.js"}'
    res = run({"a.ts": "import u from 'u'\n", "p/src/index.ts": ""}, {"p/package.json": pkg})
    assert edges(res) == {("a.ts", "p/src/index.ts")}
    assert edge(res, "a.ts", "p/src/index.ts").certain is False


def test_a_package_root_index_is_certain():
    res = run(
        {"a.ts": "import u from 'u'\n", "p/index.ts": ""}, {"p/package.json": '{"name": "u"}'}
    )
    assert edges(res) == {("a.ts", "p/index.ts")}
    assert edge(res, "a.ts", "p/index.ts").certain is True


def test_a_workspace_subpath_resolves_inside_the_package():
    res = run(
        {"a.ts": "import h from '@acme/util/helpers/h'\n", "p/helpers/h.ts": ""},
        {"p/package.json": '{"name": "@acme/util"}'},
    )
    assert edges(res) == {("a.ts", "p/helpers/h.ts")}


def test_a_name_that_is_not_in_the_repo_stays_external():
    res = run({"a.ts": "import r from 'react'\n"}, {"p/package.json": '{"name": "u"}'})
    assert edges(res) == set()
    assert res.external == 1


def test_a_malformed_package_json_is_skipped():
    res = run({"a.ts": ""}, {"package.json": "{nope"})
    assert ("package.json", "malformed") in res.skipped


# --- entries and tests ---------------------------------------------------------------


def test_package_json_entry_fields_mark_entries():
    pkg = (
        '{"name": "c", "main": "./src/index.js", "bin": {"c": "./bin/c.ts"},'
        ' "exports": {"./x": "./src/x.js"}}'
    )
    res = run(
        {"src/index.ts": "", "bin/c.ts": "", "src/x.ts": "", "src/other.ts": ""},
        {"package.json": pkg},
    )
    assert {f.path for f in res.files if f.is_entry} == {"src/index.ts", "bin/c.ts", "src/x.ts"}


def test_a_string_bin_is_an_entry():
    res = run({"cli.js": ""}, {"package.json": '{"name": "c", "bin": "./cli.js"}'})
    assert node(res, "cli.js").is_entry is True


def test_an_index_html_module_script_is_an_entry():
    html = '<html><body><script type="module" src="/src/main.tsx"></script></body></html>'
    res = run({"ui/src/main.tsx": "", "ui/src/other.ts": ""}, {"ui/index.html": html})
    assert node(res, "ui/src/main.tsx").is_entry is True
    assert node(res, "ui/src/other.ts").is_entry is False


def test_a_relative_html_script_resolves_from_the_html():
    html = "<script src='./app.js'></script>"
    res = run({"site/app.js": ""}, {"site/index.html": html})
    assert node(res, "site/app.js").is_entry is True


def test_test_files_are_tests():
    paths = ["a.test.ts", "b.spec.tsx", "src/__tests__/c.ts", "tests/d.js", "test/e.mjs", "f.ts"]
    res = run(dict.fromkeys(paths, ""))
    assert {f.path for f in res.files if f.is_test} == set(paths) - {"f.ts"}


# --- hostile manifests ------------------------------------------------------------------


def test_a_deeply_nested_manifest_is_malformed_not_fatal():
    deep = "[" * 100_000 + "]" * 100_000
    res = run({"a.ts": ""}, {"package.json": deep, "tsconfig.json": deep})
    assert ("package.json", "malformed") in res.skipped
    assert ("tsconfig.json", "malformed") in res.skipped


def test_a_page_of_unclosed_script_tags_is_fast():
    import time

    html = "<script " * 200_000
    start = time.perf_counter()
    run({"a.ts": ""}, {"index.html": html})
    assert time.perf_counter() - start < 2.0
