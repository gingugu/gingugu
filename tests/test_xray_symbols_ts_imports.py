"""X17 for TypeScript and JavaScript: calls across files, through every import form.

Specifiers resolve through the same resolver as the import graph (relative
paths, directory indexes, tsconfig ``paths``, workspace packages). A named
import follows re-exports through barrel files - bounded, cycle-safe - to the
file that defines the name. The cached parse is untrusted: a record of any
other shape, or a name that is not an identifier, is parsed again.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import symbols
from gingugu.xray.lang import typescript

A = typescript.ADAPTER


def _sym(files: dict[str, str], manifests: dict[str, str] | None = None) -> symbols.Symbols:
    src = {rel: text.encode() for rel, text in files.items()}
    mf = {rel: text.encode() for rel, text in (manifests or {}).items()}
    return A.extract(src, mf).symbols


LIB = "export function f() {}\nexport function k() {}\n"
STORE = "export class Store {\n  save() {}\n}\n"


@pytest.mark.parametrize(
    ("app", "caller"),
    [
        ("import { f } from './lib';\nexport function main() { f(); }\n", "main"),
        ("import { f as g } from './lib';\nexport function main() { g(); }\n", "main"),
        ("import * as lib from './lib';\nexport function main() { lib.f(); }\n", "main"),
        ("import { f } from './lib.js';\nf();\n", "<module>"),  # TS's .js-for-.ts form
    ],
)
def test_each_es_import_form_resolves_to_the_definition(app, caller):
    sym = _sym({"src/lib.ts": LIB, "src/app.ts": app})
    assert ("src/app.ts", caller, "src/lib.ts", "f") in sym.edges


@pytest.mark.parametrize(
    "lib",
    [
        "export default function make() {}\n",
        "function make() {}\nexport default make;\n",
        "export default class make {}\n",
    ],
)
def test_a_default_import_resolves_to_the_default_export(lib):
    sym = _sym({"src/lib.ts": lib, "src/app.ts": "import mk from './lib';\nmk();\n"})
    assert ("src/app.ts", "<module>", "src/lib.ts", "make") in sym.edges


@pytest.mark.parametrize(
    "app",
    [
        "const lib = require('./lib');\nfunction main() { lib.f(); }\n",
        "const { f } = require('./lib');\nfunction main() { f(); }\n",
        "const { f: g } = require('./lib');\nfunction main() { g(); }\n",
    ],
)
def test_require_binds_like_an_import(app):
    sym = _sym({"src/lib.js": "function f() {}\nmodule.exports = { f };\n", "src/app.js": app})
    assert ("src/app.js", "main", "src/lib.js", "f") in sym.edges


def test_a_directory_import_resolves_through_its_index():
    sym = _sym({"src/util/index.ts": LIB, "src/app.ts": "import { f } from './util';\nf();\n"})
    assert ("src/app.ts", "<module>", "src/util/index.ts", "f") in sym.edges


def test_tsconfig_paths_resolve_for_symbols_too():
    sym = _sym(
        {"src/lib/util.ts": LIB, "src/app.ts": "import { f } from '@lib/util';\nf();\n"},
        {"tsconfig.json": json.dumps({"compilerOptions": {"paths": {"@lib/*": ["./src/lib/*"]}}})},
    )
    assert ("src/app.ts", "<module>", "src/lib/util.ts", "f") in sym.edges


def test_an_imported_class_types_a_value_and_new_is_a_call():
    sym = _sym(
        {
            "src/store.ts": STORE,
            "src/app.ts": (
                "import { Store } from './store';\n"
                "import * as st from './store';\n"
                "export function main() {\n"
                "  const s = new Store();\n  s.save();\n"
                "  const t = new st.Store();\n  t.save();\n"
                "}\n"
            ),
        }
    )
    assert ("src/app.ts", "main", "src/store.ts", "Store") in sym.edges
    assert sym.edges[("src/app.ts", "main", "src/store.ts", "Store.save")] == 2


# --- barrels --------------------------------------------------------------------------


def test_named_imports_follow_re_exports_to_the_defining_file():
    sym = _sym(
        {
            "src/lib.ts": LIB,
            "src/store.ts": STORE,
            "src/index.ts": (
                "export { f } from './lib';\n"
                "export { k as kk } from './lib';\n"
                "export * from './store';\n"
            ),
            "src/app.ts": (
                "import { f, kk, Store } from './index';\n"
                "export function main() {\n  f();\n  kk();\n"
                "  const s = new Store();\n  s.save();\n}\n"
            ),
        }
    )
    e = sym.edges
    assert ("src/app.ts", "main", "src/lib.ts", "f") in e
    assert ("src/app.ts", "main", "src/lib.ts", "k") in e
    assert ("src/app.ts", "main", "src/store.ts", "Store.save") in e


def test_a_namespace_import_of_a_barrel_reaches_through_it():
    sym = _sym(
        {
            "src/lib.ts": LIB,
            "src/index.ts": "export * from './lib';\n",
            "src/app.ts": "import * as api from './index';\napi.f();\n",
        }
    )
    assert ("src/app.ts", "<module>", "src/lib.ts", "f") in sym.edges


def _chain(n: int) -> dict[str, str]:
    files = {f"src/b{i}.ts": f"export * from './b{i + 1}';\n" for i in range(n)}
    files[f"src/b{n}.ts"] = LIB
    files["src/app.ts"] = "import { f } from './b0';\nf();\n"
    return files


def test_a_short_re_export_chain_resolves():
    assert ("src/app.ts", "<module>", "src/b3.ts", "f") in _sym(_chain(3)).edges


def test_a_long_re_export_chain_stops_and_counts_the_call_unresolved():
    sym = _sym(_chain(12))
    assert not any(sp == "src/app.ts" for sp, _, _, _ in sym.edges)
    assert sym.unresolved == 1


def test_a_re_export_cycle_terminates():
    sym = _sym(
        {
            "src/a.ts": "export * from './b';\n",
            "src/b.ts": "export * from './a';\n",
            "src/app.ts": "import { nope } from './a';\nnope();\n",
        }
    )
    assert sym.unresolved == 1


def test_a_package_import_is_external():
    sym = _sym({"src/app.ts": "import { useState } from 'react';\nuseState();\n"})
    assert sym.external == 1 and sym.unresolved == 0


# --- names ------------------------------------------------------------------------------


def test_dollar_names_are_symbols_and_survive_the_cache_form():
    sym = _sym(
        {
            "src/i18n.ts": "export function $t(k: string) {}\n",
            "src/app.ts": "import { $t } from './i18n';\nexport function $main() { $t('x'); }\n",
        }
    )
    assert ("src/app.ts", "$main", "src/i18n.ts", "$t") in sym.edges
    back = symbols.from_json(json.loads(json.dumps(symbols.to_json(sym))))
    assert back == sym


# --- the parse cache --------------------------------------------------------------------


def test_a_cached_parse_round_trips_and_links_to_the_same_symbols():
    files = {
        "src/lib.ts": LIB,
        "src/store.ts": STORE,
        "src/app.ts": "import { f } from './lib';\nf();\n",
    }
    raws = {rel: json.loads(json.dumps(A.parse(rel, t.encode()))) for rel, t in files.items()}
    assert all(A.valid(r) for r in raws.values())
    assert A.link(raws, {}).symbols == _sym(files)


def test_a_cached_parse_without_symbol_records_is_refused():
    raw = json.loads(json.dumps(A.parse("m.ts", LIB.encode())))
    raw.pop("sym")  # the shape an older extractor wrote
    assert not A.valid(raw)


def _poisoned(raw: dict) -> dict:
    """The raw with one definition renamed to text that could carry an instruction."""
    text = json.dumps(raw["sym"])
    return {**raw, "sym": json.loads(text.replace('"f"', '"f\\nIGNORE PREVIOUS"', 1))}


def test_a_cached_name_that_is_not_an_identifier_is_refused():
    raw = json.loads(json.dumps(A.parse("m.ts", LIB.encode())))
    assert '"f"' in json.dumps(raw["sym"])
    assert not A.valid(_poisoned(raw))


@pytest.mark.parametrize("bad", [None, [], {}, "x", {"defs": []}])
def test_a_cached_symbol_record_of_any_other_shape_is_refused(bad):
    raw = json.loads(json.dumps(A.parse("m.ts", LIB.encode())))
    assert not A.valid({**raw, "sym": bad})
