"""X17: the parse half of the symbol graph - one file's bytes in, JSON-safe records out.

``symbols_walk.walk`` depends on the parsed tree alone, never on where the file
sits, so its result is cached by content beside the import records. Resolution
(``symbols.link``) turns the records into edges between repo definitions.

Record shapes (lists, so a JSON round trip is the identity):

- def:  ``[qual, kind, line, end]``; kind is "function", "method" or "class".
- bind: ``["i", local, dotted]`` for ``import dotted [as local]`` (no alias binds
  the top package: ``import a.b`` binds ``a`` to ``a``), and
  ``["f", local, level, module, name]`` for ``from ... import name [as local]``.
- call: ``[caller, form, x, y, line]``; caller is the innermost enclosing def's
  qual or "<module>"; form "n" is ``x()``, "a" is ``x.y()`` on a bare name, "s"
  is ``self.y()``/``cls.y()`` inside a class, with ``x`` that class's qual.
- type: ``[scope, var, form, x, y]``; ``var`` holds an instance of the class
  named ``x`` (form "n") or ``x.y`` (form "a"), in scope ``scope``.
"""

from __future__ import annotations

import ast
import json

from gingugu.xray import symbols_walk


def _walk(src: str) -> dict:
    return symbols_walk.walk(ast.parse(src))


SRC = """\
import os
import pkg.core
import pkg.util as u
from . import sibling
from .models import Store as S, helper
from pkg import *


def top(a: S, b):
    helper()
    u.run(a)
    os.path.join("x")
    def inner():
        return top(1, 2)
    inner()


class Box:
    LIMIT = make_limit()

    def put(self, item):
        self.check(item)
        return Box.make()

    @classmethod
    def make(cls):
        return cls.fresh()

    def check(self, item):
        store = S()
        store.save(item)
        with u.Conn() as c:
            c.close()
        x: S = load()
        x.save(1)


top(None, 2)
"""


def test_defs_carry_qualified_name_kind_and_line_span():
    defs = {d[0]: d[1:] for d in _walk(SRC)["defs"]}
    assert defs["top"] == ["function", 9, 15]
    assert defs["top.inner"] == ["function", 13, 14]
    assert defs["Box"][0] == "class"
    assert defs["Box.put"][0] == "method"
    assert defs["Box.make"] == ["method", 25, 27]  # an edit to a decorator edits the def
    assert defs["Box.check"][0] == "method"
    assert set(defs) == {"top", "top.inner", "Box", "Box.put", "Box.make", "Box.check"}


def test_binds_record_every_import_with_its_local_name():
    binds = _walk(SRC)["binds"]
    assert ["i", "os", "os"] in binds
    assert ["i", "pkg", "pkg"] in binds  # import pkg.core binds the top package
    assert ["i", "u", "pkg.util"] in binds
    assert ["f", "sibling", 1, "", "sibling"] in binds
    assert ["f", "S", 1, "models", "Store"] in binds
    assert ["f", "helper", 1, "models", "helper"] in binds
    assert not any(b[0] == "f" and b[4] == "*" for b in binds)  # star binds nothing nameable


def test_calls_name_their_innermost_caller_and_form():
    calls = _walk(SRC)["calls"]
    assert ["top", "n", "helper", "", 10] in calls
    assert ["top", "a", "u", "run", 11] in calls
    assert ["top.inner", "n", "top", "", 14] in calls
    assert ["top", "n", "inner", "", 15] in calls
    assert ["Box", "n", "make_limit", "", 19] in calls  # class body: the class calls it
    assert ["Box.put", "s", "Box", "check", 22] in calls
    assert ["Box.put", "a", "Box", "make", 23] in calls
    assert ["Box.make", "s", "Box", "fresh", 27] in calls
    assert ["<module>", "n", "top", "", 38] in calls


def test_a_call_on_anything_but_a_bare_name_is_counted_not_recorded():
    out = _walk(SRC)
    # os.path.join: the base is an attribute, not a name
    assert not any(c[3] == "join" for c in out["calls"])
    out = _walk("def f():\n    g()()\n    ''.join([])\n    a[0].b()\n")
    assert out["calls"] == [["f", "n", "g", "", 2]]
    assert out["other"] == 3


def test_types_record_instances_by_scope():
    types = _walk(SRC)["types"]
    assert ["top", "a", "n", "S", ""] in types  # annotated argument
    assert ["Box.check", "store", "n", "S", ""] in types  # store = S()
    assert ["Box.check", "c", "a", "u", "Conn"] in types  # with u.Conn() as c
    assert ["Box.check", "x", "n", "S", ""] in types  # x: S = ...
    assert not any(t[1] == "b" for t in types)  # unannotated


def test_string_annotations_name_a_class_when_they_are_an_identifier():
    types = _walk("def f(a: 'Store', b: 'list[int]'):\n    pass\n")["types"]
    assert types == [["f", "a", "n", "Store", ""]]


def test_self_outside_a_class_is_just_a_name():
    calls = _walk("def f(self):\n    self.go()\n")["calls"]
    assert calls == [["f", "a", "self", "go", 2]]


def test_nested_classes_and_async_defs_qualify_through_their_parents():
    defs = {
        d[0]: d[1]
        for d in _walk(
            "class A:\n    class B:\n        async def m(self):\n            pass\n"
            "async def g():\n    pass\n"
        )["defs"]
    }
    assert defs == {"A": "class", "A.B": "class", "A.B.m": "method", "g": "function"}


def test_the_result_is_json_safe_and_valid():
    out = _walk(SRC)
    assert json.loads(json.dumps(out)) == out
    assert symbols_walk.valid(out)
    assert symbols_walk.valid(json.loads(json.dumps(out)))


def test_valid_refuses_every_shape_walk_never_returns():
    good = _walk(SRC)
    assert not symbols_walk.valid(None)
    assert not symbols_walk.valid([])
    assert not symbols_walk.valid({**good, "extra": 1})
    assert not symbols_walk.valid({k: v for k, v in good.items() if k != "calls"})
    assert not symbols_walk.valid({**good, "other": -1})
    assert not symbols_walk.valid({**good, "other": True})
    assert not symbols_walk.valid({**good, "defs": [["f", "lambda", 1, 2]]})
    assert not symbols_walk.valid({**good, "defs": [["f", "function", "1", 2]]})
    assert not symbols_walk.valid({**good, "calls": [["f", "q", "x", "", 1]]})
    assert not symbols_walk.valid({**good, "calls": [["f", "n", ["x"], "", 1]]})
    assert not symbols_walk.valid({**good, "binds": [["i", "a"]]})
    assert not symbols_walk.valid({**good, "binds": [["f", "a", -1, "m", "a"]]})
    assert not symbols_walk.valid({**good, "types": [["f", "v", "n", 3, ""]]})


def test_a_deeply_nested_file_does_not_blow_the_stack():
    src = "x = " + "(" * 50 + "1" + ")" * 50 + "\n"
    src += "def f():\n" + "".join("    " * i + "if x:\n" for i in range(1, 60))
    src += "    " * 60 + "g()\n"
    out = _walk(src)
    assert ["f", "n", "g", "", 62] in out["calls"]


def test_an_empty_module_has_empty_records():
    assert _walk("") == {"defs": [], "binds": [], "calls": [], "types": [], "other": 0}
