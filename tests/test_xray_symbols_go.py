"""X17 for Go: definitions, package scope, typed values and the calls they resolve.

A Go package is a directory, so a name resolves across every file in it. A
method is ``Type.Method`` (receiver base type, through ``*`` and type
parameters). A value is typed by its receiver, a parameter, ``var x T``,
``T{}``/``&T{}``, or the declared first result of the function that made it -
which Go spells out, so ``s := store.New()`` types ``s``. Interfaces and
function values stay unresolved: counted, never guessed.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray import symbols
from gingugu.xray.lang import golang

A = golang.ADAPTER
MOD = {"go.mod": "module example.com/app\n\ngo 1.22\n"}


def _sym(files: dict[str, str], manifests: dict[str, str] | None = None) -> symbols.Symbols:
    src = {rel: text.encode() for rel, text in files.items()}
    mf = {rel: text.encode() for rel, text in (MOD if manifests is None else manifests).items()}
    return A.extract(src, mf).symbols


STORE = (
    "package store\n"  # 1
    "\n"  # 2
    "type Store struct{ n int }\n"  # 3
    "\n"  # 4
    "func New() *Store {\n"  # 5
    "\treturn &Store{}\n"  # 6
    "}\n"  # 7
    "\n"  # 8
    "func (s *Store) Save() {\n"  # 9
    "\ts.check()\n"  # 10
    "}\n"  # 11
    "\n"  # 12
    "func (s *Store) check() {}\n"  # 13
    "\n"  # 14
    "func Helper() {}\n"  # 15
)
UTIL = "package store\n\nfunc util() {\n\tHelper()\n\ts := New()\n\ts.Save()\n}\n"
MAIN = (
    "package main\n\n"
    'import (\n\t"fmt"\n\t"strings"\n\n'
    '\t"example.com/app/store"\n\tst "example.com/app/store"\n)\n\n'
    "func main() {\n"
    "\ts := store.New()\n"
    "\ts.Save()\n"
    "\tstore.Helper()\n"
    "\tst.Helper()\n"
    '\tfmt.Println(strings.ToUpper("x"), len("y"))\n'
    "\tvar v store.Store\n"
    "\tv.Save()\n"
    "\tp := &store.Store{}\n"
    "\tp.Save()\n"
    "\tkeep(s)\n"
    "}\n\n"
    "func keep(s *store.Store) {\n\ts.Save()\n}\n"
)
REPO = {"store/store.go": STORE, "store/util.go": UTIL, "main.go": MAIN}


def test_functions_methods_and_types_are_definitions_with_line_spans():
    defs = _sym(REPO).defs["store/store.go"]
    assert defs["Store"] == ("class", 3, 3)
    assert defs["New"] == ("function", 5, 7)
    assert defs["Store.Save"] == ("method", 9, 11)
    assert defs["Store.check"] == ("method", 13, 13)
    assert defs["Helper"] == ("function", 15, 15)


def test_a_receiver_calls_its_own_methods():
    assert ("store/store.go", "Store.Save", "store/store.go", "Store.check") in _sym(REPO).edges


def test_a_name_resolves_across_every_file_of_its_package():
    e = _sym(REPO).edges
    assert ("store/util.go", "util", "store/store.go", "Helper") in e
    assert ("store/util.go", "util", "store/store.go", "New") in e
    # New's declared result is *Store, so s is a Store
    assert ("store/util.go", "util", "store/store.go", "Store.Save") in e


def test_imported_calls_resolve_by_package_name_and_alias():
    e = _sym(REPO).edges
    assert ("main.go", "main", "store/store.go", "New") in e
    assert ("main.go", "main", "store/store.go", "Helper") in e
    assert ("main.go", "main", "main.go", "keep") in e
    assert ("main.go", "keep", "store/store.go", "Store.Save") in e


def test_values_are_typed_by_constructor_result_var_literal_and_parameter():
    # s := store.New(), var v store.Store, p := &store.Store{} - three typed values in main
    assert _sym(REPO).edges[("main.go", "main", "store/store.go", "Store.Save")] == 3


def test_every_call_is_counted_as_resolved_external_or_unresolved():
    sym = _sym({"store/store.go": STORE, "main.go": MAIN})
    # main.go: 8 resolved (New, s.Save, Helper x2, v.Save, p.Save, keep, keep's s.Save),
    # 3 external (fmt.Println, strings.ToUpper, len); store.go: s.check resolved.
    assert sym.resolved == 9
    assert sym.external == 3
    assert sym.unresolved == 0


def test_interfaces_and_function_values_stay_unresolved():
    sym = _sym(
        {
            "store/store.go": STORE,
            "store/use.go": (
                'package store\n\nimport "io"\n\n'
                "type Saver interface{ Save() }\n\n"
                "func use(sv Saver, f func(), r io.Reader) {\n"
                "\tsv.Save()\n"  # an interface: unresolved
                "\tf()\n"  # a function value: unresolved
                "\tr.Read(nil)\n"  # a type from outside the repo: external
                "\tmystery()\n"  # nothing binds it: unresolved
                "}\n"
            ),
        }
    )
    assert not any(sp == "store/use.go" for sp, _, _, _ in sym.edges)
    assert sym.unresolved == 3
    assert sym.external == 1


def test_a_package_whose_name_is_not_its_directory_resolves_by_its_clause():
    sym = _sym(
        {
            "internal/v2/api.go": "package api\n\nfunc Do() {}\n",
            "cmd/tool/main.go": (
                'package main\n\nimport "example.com/app/internal/v2"\n\n'
                "func main() {\n\tapi.Do()\n}\n"
            ),
        }
    )
    assert ("cmd/tool/main.go", "main", "internal/v2/api.go", "Do") in sym.edges


def test_tests_see_their_package_from_inside_and_from_an_external_test_package():
    sym = _sym(
        {
            **REPO,
            "store/internal_test.go": (
                'package store\n\nimport "testing"\n\n'
                "func TestHelper(t *testing.T) {\n\tHelper()\n\tutil()\n}\n"
            ),
            "store/store_test.go": (
                'package store_test\n\nimport (\n\t"testing"\n\n\t"example.com/app/store"\n)\n\n'
                "func TestSave(t *testing.T) {\n\ts := store.New()\n\ts.Save()\n\tt.Fatal()\n}\n"
            ),
        }
    )
    e = sym.edges
    assert ("store/internal_test.go", "TestHelper", "store/store.go", "Helper") in e
    assert ("store/internal_test.go", "TestHelper", "store/util.go", "util") in e
    assert ("store/store_test.go", "TestSave", "store/store.go", "New") in e
    assert ("store/store_test.go", "TestSave", "store/store.go", "Store.Save") in e


def test_a_generic_receiver_names_its_base_type():
    sym = _sym(
        {
            "list/list.go": (
                "package list\n\n"
                "type List[T any] struct{ xs []T }\n\n"
                "func (l *List[T]) Push(v T) {}\n\n"
                "func g() {\n\tl := &List[int]{}\n\tl.Push(1)\n}\n"
            )
        }
    )
    assert "List.Push" in sym.defs["list/list.go"]
    assert ("list/list.go", "g", "list/list.go", "List.Push") in sym.edges


def test_a_package_level_variable_types_calls_in_another_file():
    sym = _sym(
        {
            "store/store.go": STORE,
            "store/default.go": "package store\n\nvar Default = New()\n",
            "store/run.go": "package store\n\nfunc run() {\n\tDefault.Save()\n}\n",
        }
    )
    assert ("store/run.go", "run", "store/store.go", "Store.Save") in sym.edges


def test_a_directory_outside_any_module_is_still_one_package():
    sym = _sym(
        {
            "scripts/a.go": "package main\n\nfunc a() {\n\tb()\n}\n",
            "scripts/b.go": "package main\n\nfunc b() {}\n",
        },
        manifests={},
    )
    assert ("scripts/a.go", "a", "scripts/b.go", "b") in sym.edges


def test_callers_answer_for_a_go_method():
    out = symbols.callers(_sym(REPO), "store/store.go", "Store.Save")
    assert {(d["path"], d["symbol"]) for d in out["direct"]} == {
        ("main.go", "main"),
        ("main.go", "keep"),
        ("store/util.go", "util"),
    }


# --- the parse cache --------------------------------------------------------------------


def test_a_cached_parse_round_trips_and_links_to_the_same_symbols():
    raws = {rel: json.loads(json.dumps(A.parse(rel, t.encode()))) for rel, t in REPO.items()}
    assert all(A.valid(r) for r in raws.values())
    mf = {k: v.encode() for k, v in MOD.items()}
    assert A.link(raws, mf).symbols == _sym(REPO)


def test_a_cached_parse_without_symbol_records_is_refused():
    raw = json.loads(json.dumps(A.parse("store/store.go", STORE.encode())))
    raw.pop("sym")  # the shape an older extractor wrote
    assert not A.valid(raw)


@pytest.mark.parametrize("bad", [None, [], {}, "x", {"defs": []}])
def test_a_cached_symbol_record_of_any_other_shape_is_refused(bad):
    raw = json.loads(json.dumps(A.parse("store/store.go", STORE.encode())))
    assert not A.valid({**raw, "sym": bad})


@pytest.mark.parametrize(
    ("key", "rec"),
    [
        ("defs", ["Helper", ["function"], 1, 2]),  # unhashable where a kind belongs
        ("defs", ["Helper", {}, 1, 2]),
        ("types", ["Helper", "s", [], "", "Store"]),  # unhashable where a form belongs
        ("types", ["Helper", "s", {"t": 1}, "", "Store"]),
    ],
)
def test_an_unhashable_cached_field_is_refused_not_raised(key, rec):
    raw = json.loads(json.dumps(A.parse("store/store.go", STORE.encode())))
    raw["sym"][key].append(rec)
    assert A.valid(raw) is False


def test_a_cached_name_that_is_not_an_identifier_is_refused():
    raw = json.loads(json.dumps(A.parse("store/store.go", STORE.encode())))
    text = json.dumps(raw["sym"]).replace('"Helper"', '"Helper\\nIGNORE PREVIOUS"', 1)
    assert text != json.dumps(raw["sym"])
    assert not A.valid({**raw, "sym": json.loads(text)})


def test_a_value_declared_three_times_stays_unresolved_and_never_crashes():
    # Found on gin: a name re-declared with a second type, then a third time. The
    # second makes it ambiguous; the third must not index into that verdict.
    sym = _sym(
        {
            "store/store.go": STORE,
            "store/run.go": (
                "package store\n\nfunc run() {\n"
                "\tvar s Store\n\ts.Save()\n"
                "\tvar s Other\n"
                "\tvar s Store\n"
                "\ts.Save()\n}\n\ntype Other struct{}\n"
            ),
        }
    )
    assert not any(sp == "store/run.go" for sp, _, _, _ in sym.edges)
    assert sym.unresolved == 2
