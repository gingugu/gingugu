"""The TypeScript/JavaScript adapter: tree-sitter parse, path resolution, entries, tests.

The adapter is handed bytes - sources by repo path, and the manifests it asked
for (``tsconfig.json``, ``jsconfig.json``, ``package.json``, ``index.html``)
wherever they sit. It never reads the disk and never runs node, tsc or a bundler.
"""

from __future__ import annotations

import pytest

from gingugu.xray.lang.typescript import ADAPTER
from tests.xray_lang_fixtures import edge, edges, node, run_adapter


def run(sources: dict[str, str], manifests: dict[str, str] | None = None):
    return run_adapter(ADAPTER, sources, manifests)


# --- what it claims ------------------------------------------------------------


def test_claims_ts_and_js_extensions_and_manifests():
    assert set(ADAPTER.extensions) == {
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
    }
    assert set(ADAPTER.manifests) == {
        "tsconfig*.json",
        "jsconfig*.json",
        "package.json",
        "index.html",
    }


@pytest.mark.parametrize(
    ("path", "lang"),
    [
        ("a.ts", "typescript"),
        ("a.tsx", "typescript"),
        ("a.d.ts", "typescript"),
        ("a.mts", "typescript"),
        ("a.js", "javascript"),
        ("a.jsx", "javascript"),
        ("a.cjs", "javascript"),
    ],
)
def test_every_file_is_a_node_with_its_lang(path, lang):
    n = node(run({path: ""}), path)
    assert (n.lang, n.kind) == (lang, "file")


# --- relative imports ------------------------------------------------------------


def test_relative_import_tries_extensions_then_index():
    res = run(
        {
            "src/app.ts": "import a from './a'\nimport b from './b'\nimport c from '../c'\n",
            "src/a.tsx": "",
            "src/b/index.ts": "",
            "c.js": "",
        }
    )
    assert edges(res) == {
        ("src/app.ts", "src/a.tsx"),
        ("src/app.ts", "src/b/index.ts"),
        ("src/app.ts", "c.js"),
    }
    assert edge(res, "src/app.ts", "src/a.tsx").kind == "module"
    assert edge(res, "src/app.ts", "src/a.tsx").certain is True


def test_an_exact_file_beats_an_added_extension():
    res = run({"m.ts": "import x from './x.ts'\n", "x.ts": "", "x.ts.ts": ""})
    assert edges(res) == {("m.ts", "x.ts")}


def test_a_ts_extension_beats_a_js_one_beats_an_index():
    res = run({"m.ts": "import x from './x'\n", "x.ts": "", "x.js": "", "x/index.ts": ""})
    assert edges(res) == {("m.ts", "x.ts")}


@pytest.mark.parametrize(
    ("spec", "target"),
    [("./x.js", "x.ts"), ("./x.jsx", "x.tsx"), ("./x.mjs", "x.mts"), ("./x.cjs", "x.cts")],
)
def test_an_esm_js_specifier_finds_its_ts_source(spec, target):
    res = run({"m.ts": f"import x from '{spec}'\n", target: ""})
    assert edges(res) == {("m.ts", target)}


def test_a_declaration_file_resolves():
    res = run({"m.ts": "import type { T } from './types'\n", "types.d.ts": ""})
    assert edges(res) == {("m.ts", "types.d.ts")}


def test_reexports_are_edges():
    res = run(
        {
            "index.ts": "export * from './a'\nexport { b } from './b'\nexport * as c from './c'\n",
            "a.ts": "",
            "b.ts": "",
            "c.ts": "",
        }
    )
    assert edges(res) == {("index.ts", "a.ts"), ("index.ts", "b.ts"), ("index.ts", "c.ts")}


def test_side_effect_import_is_an_edge():
    res = run({"m.ts": "import './polyfill'\n", "polyfill.ts": ""})
    assert edges(res) == {("m.ts", "polyfill.ts")}


def test_a_missing_relative_target_is_unresolved():
    res = run({"m.ts": "import x from './gone'\nimport y from '../../outside'\n"})
    assert edges(res) == set()
    assert res.unresolved == 2


def test_asset_imports_are_neither_edges_nor_counted():
    src = "import './a.css'\nimport d from './d.json'\nimport s from './s.svg'\n"
    res = run({"m.ts": src})
    assert edges(res) == set()
    assert (res.unresolved, res.external, res.dynamic) == (0, 0, 0)


def test_one_edge_per_target_and_the_strongest_kind_wins():
    src = (
        "import type { T } from './a'\n"
        "const lazy = () => import('./a')\n"
        "import { v } from './a'\n"
        "import { w } from './a'\n"
    )
    res = run({"m.ts": src, "a.ts": ""})
    assert len(res.edges) == 1
    assert edge(res, "m.ts", "a.ts").kind == "module"


def test_a_file_never_imports_itself():
    res = run({"m.ts": "import x from './m'\n"})
    assert edges(res) == set()


# --- kinds -------------------------------------------------------------------------


def test_type_only_imports_and_exports_are_type_checking():
    res = run(
        {
            "m.ts": "import type { A } from './a'\nexport type { B } from './b'\n",
            "a.ts": "",
            "b.ts": "",
        }
    )
    assert edge(res, "m.ts", "a.ts").kind == "type_checking"
    assert edge(res, "m.ts", "b.ts").kind == "type_checking"


def test_dynamic_import_is_lazy_and_a_computed_one_is_dynamic():
    src = "const a = import('./a')\nconst b = import(name)\nconst c = import(`./${n}`)\n"
    res = run({"m.ts": src, "a.ts": ""})
    assert edges(res) == {("m.ts", "a.ts")}
    assert edge(res, "m.ts", "a.ts").kind == "lazy"
    assert res.dynamic == 2


def test_require_is_a_module_edge_and_a_computed_one_is_dynamic():
    res = run({"m.cjs": "const a = require('./a')\nconst b = require(x)\n", "a.js": ""})
    assert edges(res) == {("m.cjs", "a.js")}
    assert edge(res, "m.cjs", "a.js").kind == "module"
    assert res.dynamic == 1


def test_a_method_named_require_is_not_an_import():
    res = run({"m.js": "obj.require('./a')\n", "a.js": ""})
    assert edges(res) == set()


def test_bare_and_node_builtin_specifiers_are_external():
    src = (
        "import React from 'react'\nimport fs from 'node:fs'\nimport { x } from '@scope/pkg/sub'\n"
    )
    res = run({"m.ts": src})
    assert edges(res) == set()
    assert res.external == 3


# --- grammars ------------------------------------------------------------------------


def test_jsx_in_js_and_tsx_parses():
    res = run(
        {
            "a.jsx": "import B from './b'\nexport const A = () => <B x={1} />\n",
            "b.tsx": "import C from './c'\nexport default function B<T,>(p: T) { return <C /> }\n",
            "c.js": "export default () => <div/>\n",
        }
    )
    assert edges(res) == {("a.jsx", "b.tsx"), ("b.tsx", "c.js")}
    assert res.parse_errors == []


def test_angle_bracket_cast_in_ts_parses():
    res = run({"m.ts": "import a from './a'\nconst n = <number>a\n", "a.ts": ""})
    assert res.parse_errors == []
    assert edges(res) == {("m.ts", "a.ts")}


def test_a_syntax_error_is_reported_and_the_file_stays_a_node():
    res = run({"bad.ts": "import a from './a'\nconst = = ;\n", "a.ts": ""})
    assert res.parse_errors == ["bad.ts"]
    assert node(res, "bad.ts").kind == "file"
