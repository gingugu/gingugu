"""X17: symbol resolution - walk records from every file in, call edges between definitions out.

A call resolves only through what static reading can prove: a definition in an
enclosing scope, an import binding, a class named by an annotation or a
constructor call in scope, or ``self``/``cls`` inside a class. Everything else
is counted, never guessed, so the graph is a lower bound and says so.
"""

from __future__ import annotations

import ast
import json

import pytest

from gingugu.xray import symbols, symbols_walk
from gingugu.xray.resolve import ModuleIndex


def _link(files: dict[str, str]) -> symbols.Symbols:
    raws = {rel: symbols_walk.walk(ast.parse(src)) for rel, src in files.items()}
    return symbols.link(raws, ModuleIndex(list(files)))


def _edges(sym: symbols.Symbols) -> set[tuple[str, str, str, str]]:
    return set(sym.edges)


LIB = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "def f():\n    pass\n\n"
        "class Store:\n"
        "    def save(self):\n        self.check()\n\n"
        "    def check(self):\n        pass\n\n"
        "    @classmethod\n    def open(cls):\n        return cls.fresh()\n"
    ),
}


def test_an_imported_function_call_is_an_edge_to_its_definition():
    sym = _link({**LIB, "app.py": "from pkg.core import f\n\ndef main():\n    f()\n"})
    assert ("app.py", "main", "pkg/core.py", "f") in _edges(sym)


def test_module_attribute_calls_resolve_through_every_import_form():
    sym = _link(
        {
            **LIB,
            "a.py": "from pkg import core\n\ndef go():\n    core.f()\n",
            "b.py": "import pkg.core as c\n\ndef go():\n    c.f()\n",
            "pkg/c.py": "from . import core\n\ndef go():\n    core.f()\n",
        }
    )
    e = _edges(sym)
    assert {("a.py", "go", "pkg/core.py", "f"), ("b.py", "go", "pkg/core.py", "f")} <= e
    assert ("pkg/c.py", "go", "pkg/core.py", "f") in e


def test_a_constructor_call_is_an_edge_to_the_class():
    sym = _link({**LIB, "app.py": "from pkg.core import Store\n\nStore()\n"})
    assert ("app.py", "<module>", "pkg/core.py", "Store") in _edges(sym)


def test_methods_resolve_through_a_type_in_scope():
    sym = _link(
        {
            **LIB,
            "app.py": (
                "from pkg import core\nfrom pkg.core import Store\n\n"
                "def a():\n    s = Store()\n    s.save()\n\n"
                "def b(s: Store):\n    s.check()\n\n"
                "def c():\n    with core.Store() as s:\n        s.save()\n\n"
                "def d(s: 'Store'):\n    s.save()\n"
            ),
        }
    )
    e = _edges(sym)
    assert ("app.py", "a", "pkg/core.py", "Store.save") in e
    assert ("app.py", "b", "pkg/core.py", "Store.check") in e
    assert ("app.py", "c", "pkg/core.py", "Store.save") in e
    assert ("app.py", "d", "pkg/core.py", "Store.save") in e


def test_a_type_binds_only_in_its_scope_and_the_scopes_it_encloses():
    sym = _link(
        {
            **LIB,
            "app.py": (
                "from pkg.core import Store\n\n"
                "def a():\n    s = Store()\n    def inner():\n        s.save()\n\n"
                "def b(s):\n    s.save()\n"
            ),
        }
    )
    e = _edges(sym)
    assert ("app.py", "a.inner", "pkg/core.py", "Store.save") in e  # a closure sees a's s
    assert not any(src_q == "b" for _, src_q, _, _ in e)  # b's s is untyped


def test_self_and_cls_calls_resolve_inside_their_class():
    e = _edges(_link(LIB))
    assert ("pkg/core.py", "Store.save", "pkg/core.py", "Store.check") in e
    # fresh is not defined on Store (inherited, or a typo): counted, not guessed
    assert not any(dst_q.endswith("fresh") for _, _, _, dst_q in e)


def test_class_attribute_calls_resolve_through_an_imported_class():
    sym = _link({**LIB, "app.py": "from pkg.core import Store\n\ndef go():\n    Store.open()\n"})
    assert ("app.py", "go", "pkg/core.py", "Store.open") in _edges(sym)


def test_a_bare_name_in_a_method_does_not_see_the_class_body():
    sym = _link(
        {
            "m.py": (
                "def check():\n    pass\n\n"
                "class Box:\n    def check(self):\n        pass\n\n"
                "    def put(self):\n        check()\n"
            )
        }
    )
    e = _edges(sym)
    assert ("m.py", "Box.put", "m.py", "check") in e
    assert ("m.py", "Box.put", "m.py", "Box.check") not in e


def test_nested_functions_resolve_innermost_first():
    sym = _link(
        {
            "m.py": (
                "def helper():\n    pass\n\n"
                "def outer():\n    def helper():\n        pass\n    helper()\n"
            )
        }
    )
    e = _edges(sym)
    assert ("m.py", "outer", "m.py", "outer.helper") in e
    assert ("m.py", "outer", "m.py", "helper") not in e


def test_a_name_that_is_a_submodule_is_not_a_call_target():
    sym = _link({**LIB, "app.py": "from pkg import core\n\ncore()\n"})
    assert not any(d == "pkg/core.py" for _, _, d, _ in _edges(sym))


def test_recursion_is_not_an_edge():
    sym = _link({"m.py": "def f(n):\n    return f(n - 1)\n"})
    assert _edges(sym) == set()


def test_repeated_calls_are_one_edge_with_a_site_count():
    sym = _link({**LIB, "app.py": "from pkg.core import f\n\ndef go():\n    f()\n    f()\n"})
    assert sym.edges[("app.py", "go", "pkg/core.py", "f")] == 2


def test_every_call_is_counted_as_resolved_external_or_unresolved():
    sym = _link(
        {
            **LIB,
            "app.py": (
                "import json\nfrom os import path\nfrom pkg.core import f\n\n"
                "def go(x):\n"
                "    f()\n"  # resolved
                "    len(x)\n"  # builtin: external
                "    json.dumps(x)\n"  # external module
                "    path.join(x)\n"  # external module
                "    x.run()\n"  # untyped value: unresolved
                "    mystery()\n"  # nothing binds it: unresolved
            ),
        }
    )
    app_only = _link({"app.py": "def go():\n    pass\n"})
    assert app_only.resolved == app_only.external == app_only.unresolved == 0
    # LIB contributes self.check (resolved) and cls.fresh (unresolved)
    assert sym.resolved == 2
    assert sym.external == 3
    assert sym.unresolved == 3


def test_definitions_are_kept_per_file():
    sym = _link(LIB)
    assert sym.defs["pkg/core.py"]["Store.save"] == ("method", 5, 6)
    assert sym.defs["pkg/core.py"]["Store"][0] == "class"
    assert sym.defs["pkg/__init__.py"] == {}


# --- callers -----------------------------------------------------------------

CHAIN = {
    **LIB,
    "svc.py": "from pkg.core import f\n\ndef serve():\n    f()\n\ndef other():\n    serve()\n",
    "cli.py": "import svc\n\ndef main():\n    svc.serve()\n",
    "tests/test_svc.py": "import svc\n\ndef test_serve():\n    svc.serve()\n",
}


def test_callers_lists_direct_callers_and_the_transitive_reach():
    out = symbols.callers(_link(CHAIN), "pkg/core.py", "f")
    assert out["kind"] == "function"
    assert out["line"] == 1 and out["end"] == 2
    assert out["direct"] == [{"path": "svc.py", "symbol": "serve", "sites": 1}]
    assert out["direct_total"] == 1
    reach = {(r["path"], r["symbol"]) for r in out["reach"]}
    assert reach == {
        ("svc.py", "serve"),
        ("svc.py", "other"),
        ("cli.py", "main"),
        ("tests/test_svc.py", "test_serve"),
    }
    assert out["reach_total"] == 4
    assert out["reach_files"] == ["cli.py", "svc.py", "tests/test_svc.py"]
    hops = {(r["path"], r["symbol"]): r["hops"] for r in out["reach"]}
    assert hops[("svc.py", "serve")] == 1 and hops[("cli.py", "main")] == 2


def test_callers_survive_a_cycle():
    sym = _link({"m.py": "def a():\n    b()\n\ndef b():\n    a()\n"})
    out = symbols.callers(sym, "m.py", "a")
    assert [r["symbol"] for r in out["reach"]] == ["b"]


def test_callers_of_an_unknown_symbol_is_none():
    sym = _link(CHAIN)
    assert symbols.callers(sym, "pkg/core.py", "nope") is None
    assert symbols.callers(sym, "nope.py", "f") is None


def test_callers_pages_with_limit_but_totals_the_whole_set():
    out = symbols.callers(_link(CHAIN), "pkg/core.py", "f", limit=1)
    assert len(out["reach"]) == 1
    assert out["reach_total"] == 4


# --- enclosing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "want"),
    [
        (5, 5, ["Store.save"]),  # inside one method
        (5, 9, ["Store.save", "Store.check"]),  # across two methods
        (4, 4, ["Store"]),  # the class line itself
        (1, 2, ["f"]),
        (3, 3, []),  # a blank line between defs
    ],
)
def test_enclosing_names_the_innermost_definitions_an_edit_touches(start, end, want):
    assert symbols.enclosing(_link(LIB), "pkg/core.py", start, end) == want


def test_enclosing_in_an_unknown_file_is_empty():
    assert symbols.enclosing(_link(LIB), "nope.py", 1, 1) == []


# --- cache form ----------------------------------------------------------------


def test_the_cache_form_round_trips_through_json():
    sym = _link(CHAIN)
    back = symbols.from_json(json.loads(json.dumps(symbols.to_json(sym))))
    assert back == sym


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"defs": {}, "edges": [], "resolved": 0, "external": 0},
        {
            "defs": {"a.py": {"f": ["function", 1]}},
            "edges": [],
            "resolved": 0,
            "external": 0,
            "unresolved": 0,
        },
        {"defs": {}, "edges": [["a", "f", "b"]], "resolved": 0, "external": 0, "unresolved": 0},
        {"defs": {}, "edges": [], "resolved": -1, "external": 0, "unresolved": 0},
        {"defs": {}, "edges": [], "resolved": True, "external": 0, "unresolved": 0},
    ],
)
def test_a_cache_form_of_any_other_shape_is_refused(bad):
    assert symbols.from_json(bad) is None
