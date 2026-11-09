"""X17 for TypeScript and JavaScript: definitions, scopes, types and the calls they resolve.

The same records and the same promise as Python: a call resolves only through
what static reading can prove - a definition in an enclosing scope, an import
binding, a class named by ``new`` or an annotation, ``this`` inside a class.
Everything else is counted (external or unresolved), never guessed, so the
graph is a lower bound. Imports and re-exports live in ``test_xray_symbols_ts_imports``.
"""

from __future__ import annotations

from gingugu.xray import symbols
from gingugu.xray.lang import typescript


def _sym(files: dict[str, str], manifests: dict[str, str] | None = None) -> symbols.Symbols:
    src = {rel: text.encode() for rel, text in files.items()}
    mf = {rel: text.encode() for rel, text in (manifests or {}).items()}
    return typescript.ADAPTER.extract(src, mf).symbols


STORE = (
    "export class Store {\n"  # 1
    "  save() {\n"  # 2
    "    this.check();\n"  # 3
    "  }\n"  # 4
    "  check() {}\n"  # 5
    "  static open() {\n"  # 6
    "    return new Store();\n"  # 7
    "  }\n"  # 8
    "  reset() {\n"  # 9
    "    this.fresh();\n"  # 10
    "  }\n"  # 11
    "}\n"  # 12
)
LIB = (
    "export function f() {\n"  # 1
    "  return 1;\n"  # 2
    "}\n"  # 3
    "export const g = () => f();\n"  # 4
    "export const h = function () {\n"  # 5
    "  return g();\n"  # 6
    "};\n"  # 7
)


# --- definitions -------------------------------------------------------------------


def test_functions_arrows_classes_and_methods_are_definitions_with_line_spans():
    sym = _sym({"src/store.ts": STORE, "src/lib.ts": LIB})
    assert sym.defs["src/lib.ts"]["f"] == ("function", 1, 3)
    assert sym.defs["src/lib.ts"]["g"] == ("function", 4, 4)
    assert sym.defs["src/lib.ts"]["h"] == ("function", 5, 7)
    assert sym.defs["src/store.ts"]["Store"] == ("class", 1, 12)
    assert sym.defs["src/store.ts"]["Store.save"] == ("method", 2, 4)
    assert sym.defs["src/store.ts"]["Store.open"] == ("method", 6, 8)


def test_every_script_extension_carries_symbols():
    files = {
        f"m{ext}": "function a() { b(); }\nfunction b() {}\n"
        for ext in typescript.ADAPTER.extensions
    }
    files["view.tsx"] = "function A() { return <div>{b()}</div>; }\nfunction b() {}\n"
    sym = _sym(files)
    for rel in files:
        assert "b" in sym.defs[rel], rel
    assert ("view.tsx", "A", "view.tsx", "b") in sym.edges


# --- calls inside one file -------------------------------------------------------------


def test_calls_inside_a_file_are_edges_from_the_enclosing_definition():
    sym = _sym({"src/lib.ts": LIB})
    assert ("src/lib.ts", "g", "src/lib.ts", "f") in sym.edges
    assert ("src/lib.ts", "h", "src/lib.ts", "g") in sym.edges


def test_this_calls_resolve_to_the_classes_own_methods():
    sym = _sym({"src/store.ts": STORE})
    assert ("src/store.ts", "Store.save", "src/store.ts", "Store.check") in sym.edges
    # fresh is not defined on Store (inherited, or a typo): counted, not guessed
    assert not any(dq.endswith("fresh") for _, _, _, dq in sym.edges)


def test_new_is_a_call_to_the_class():
    sym = _sym({"src/store.ts": STORE})
    assert ("src/store.ts", "Store.open", "src/store.ts", "Store") in sym.edges


def test_an_anonymous_callback_belongs_to_the_definition_around_it():
    sym = _sym(
        {
            "m.ts": (
                "function f() {}\n"
                "export function main(xs: number[]) {\n  xs.forEach(() => f());\n}\n"
            )
        }
    )
    assert ("m.ts", "main", "m.ts", "f") in sym.edges


def test_a_bare_name_in_a_method_does_not_see_the_class_body():
    sym = _sym(
        {
            "m.ts": (
                "function check() {}\n"
                "class Box {\n  check() {}\n  put() {\n    check();\n  }\n}\n"
            )
        }
    )
    assert ("m.ts", "Box.put", "m.ts", "check") in sym.edges
    assert ("m.ts", "Box.put", "m.ts", "Box.check") not in sym.edges


def test_nested_functions_resolve_innermost_first():
    sym = _sym(
        {
            "m.ts": (
                "function helper() {}\n"
                "function outer() {\n  function helper() {}\n  helper();\n}\n"
            )
        }
    )
    assert ("m.ts", "outer", "m.ts", "outer.helper") in sym.edges
    assert ("m.ts", "outer", "m.ts", "helper") not in sym.edges


def test_recursion_is_not_an_edge_and_repeats_are_site_counts():
    sym = _sym({"m.ts": "function f(n) { return f(n - 1); }\nfunction g() { f(1); f(2); }\n"})
    assert ("m.ts", "f", "m.ts", "f") not in sym.edges
    assert sym.edges[("m.ts", "g", "m.ts", "f")] == 2


# --- typed values ------------------------------------------------------------------------


def test_methods_resolve_through_a_type_in_scope():
    sym = _sym(
        {
            "src/store.ts": STORE,
            "src/app.ts": (
                "import { Store } from './store';\n"
                "export function a() {\n  const s = new Store();\n  s.save();\n}\n"
                "export function b(s: Store) {\n  s.check();\n}\n"
                "export function c(s?: Store) {\n  s.save();\n}\n"
                "function make(): Store {\n  return new Store();\n}\n"
                "export function d() {\n  const s = make();\n  s.save();\n}\n"
            ),
        }
    )
    e = sym.edges
    assert ("src/app.ts", "a", "src/store.ts", "Store.save") in e
    assert ("src/app.ts", "b", "src/store.ts", "Store.check") in e
    assert ("src/app.ts", "c", "src/store.ts", "Store.save") in e
    # a function's declared return type types what it returns
    assert ("src/app.ts", "d", "src/store.ts", "Store.save") in e


def test_a_type_binds_only_in_its_scope_and_the_scopes_it_encloses():
    sym = _sym(
        {
            "src/store.ts": STORE,
            "src/app.ts": (
                "import { Store } from './store';\n"
                "function a() {\n  const s = new Store();\n"
                "  function inner() {\n    s.save();\n  }\n}\n"
                "function b(s) {\n  s.save();\n}\n"
            ),
        }
    )
    assert ("src/app.ts", "a.inner", "src/store.ts", "Store.save") in sym.edges
    assert not any(sq == "b" for _, sq, _, _ in sym.edges)


# --- counting -----------------------------------------------------------------------------


def test_every_call_is_counted_as_resolved_external_or_unresolved():
    sym = _sym(
        {
            "src/app.ts": (
                "import express from 'express';\n"
                "import { join } from 'node:path';\n"
                "function local() {}\n"
                "export function main(name: string, n: number, cb) {\n"
                "  local();\n"  # resolved
                "  const app = express();\n"  # external import
                "  app.listen(3000);\n"  # a value an external call returned: external
                "  const router = express.Router();\n"  # external
                "  router.get('/');\n"  # external
                "  join('a', 'b');\n"  # external
                "  console.log(name.trim());\n"  # global + primitive: 2 external
                "  const s = 'x';\n"
                "  s.toUpperCase();\n"  # primitive: external
                "  JSON.stringify({});\n"  # global: external
                "  n.toFixed();\n"  # primitive: external
                "  cb();\n"  # a callback: unresolved
                "  mystery();\n"  # nothing binds it: unresolved
                "  cb.run();\n"  # untyped value: unresolved
                "}\n"
            )
        }
    )
    assert sym.resolved == 1
    assert sym.external == 10
    assert sym.unresolved == 3


def test_an_empty_file_counts_nothing():
    sym = _sym({"m.ts": "export {};\n"})
    assert sym.resolved == sym.external == sym.unresolved == 0
    assert sym.defs["m.ts"] == {}


def test_callers_answer_for_a_typescript_symbol():
    sym = _sym({"src/lib.ts": LIB})
    out = symbols.callers(sym, "src/lib.ts", "f")
    assert out["direct"] == [{"path": "src/lib.ts", "symbol": "g", "sites": 1}]
    assert {r["symbol"] for r in out["reach"]} == {"g", "h"}
