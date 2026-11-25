"""X7d: an AWS Lambda handler is an entry point.

A Lambda handler is a module-level ``def <name>(event, context)``; the runtime
calls it by the ``handler = "module.function"`` string in Terraform, SAM or
serverless config. Without this kind, a repo with 23 handlers came out
as 4 entries and 33 of 34 sinks "unreached". The walk records it under the
label ``aws.lambda``, which no decorator can produce (``x.lambda`` is a syntax
error), so the kind can never be claimed by real code.
"""

from __future__ import annotations

import ast
import json

import pytest

from gingugu.xray import surface, surface_catalog, surface_records, symbols_walk
from gingugu.xray.lang import python


def _entries(src: str) -> list:
    return symbols_walk.walk(ast.parse(src))["entries"]


def test_lambda_is_a_kind_and_its_label_is_known():
    assert "lambda" in surface_catalog.KINDS
    assert surface_catalog.entry_kind("python", "aws.lambda") == "lambda"
    assert surface_catalog.entry_kind("typescript", "aws.lambda") is None
    assert surface_catalog.entry_kind("go", "aws.lambda") is None


def test_a_module_level_event_context_def_is_an_entry():
    src = "import json\n\ndef lambda_handler(event, context):\n    return 1\n"
    assert _entries(src) == [
        ["<module>", "aws.lambda", "d", "lambda_handler", "", 3, 0, 0, "", "", ""]
    ]


@pytest.mark.parametrize(
    "params",
    ["event, context", "event, _context", "event, ctx", "evt, ctx", "event, _", "_event, _ctx"],
)
def test_the_common_spellings_count(params):
    prefix = "async def" if params == "evt, ctx" else "def"  # async handlers count too
    src = f"{prefix} handle({params}):\n    pass\n"
    assert [e[1] for e in _entries(src)] == ["aws.lambda"]


@pytest.mark.parametrize(
    "src",
    [
        "def f(event):\n    pass\n",  # one parameter
        "def f(event, context, extra):\n    pass\n",  # three
        "def f(context, event):\n    pass\n",  # the wrong way round
        "def f(event, context=None):\n    pass\n",  # a default: not the runtime's shape
        "def f(event, *, context):\n    pass\n",  # keyword-only
        "def f(event, context, *args):\n    pass\n",
        "def f(event, context, **kw):\n    pass\n",
        "def f(request, context):\n    pass\n",  # gRPC servicer shape
        "class H:\n    def handle(self, event, context):\n        pass\n",  # a method
        "class H:\n    def handle(event, context):\n        pass\n",  # still a method
        "def outer():\n    def inner(event, context):\n        pass\n",  # nested
        "if True:\n    pass\nf = lambda event, context: 1\n",  # a lambda expression
    ],
)
def test_other_shapes_are_not_entries(src):
    assert _entries(src) == []


def test_a_decorated_handler_is_one_entry_per_kind():
    src = "@app.get('/x')\ndef lambda_handler(event, context):\n    pass\n"
    assert sorted(e[1] for e in _entries(src)) == ["app.get", "aws.lambda"]


def test_the_record_round_trips_through_validation():
    src = "def lambda_handler(event, context):\n    return 1\n"
    raw = python.ADAPTER.parse("app.py", src.encode())
    assert python.ADAPTER.valid(json.loads(json.dumps(raw)))
    (rec,) = _entries(src)
    assert surface_records.is_entry(rec, "python")
    assert symbols_walk.valid(json.loads(json.dumps(symbols_walk.walk(ast.parse(src)))))


def test_a_handler_reaches_what_it_calls():
    src = (
        "import subprocess\n"  # 1
        "def run(cmd):\n"  # 2
        "    subprocess.run(cmd)\n"  # 3
        "def lambda_handler(event, context):\n"  # 4
        "    run(event['cmd'])\n"  # 5
    )
    sym = python.ADAPTER.extract({"app.py": src.encode()}, {}).symbols
    data = surface.build(sym)
    ((e, found),) = data["entries"]
    assert (e.kind, e.label, e.handler, e.line) == ("lambda", "aws.lambda", "lambda_handler", 4)
    assert [(r.sink, r.hops) for r in found] == [("subprocess.run", 1)]
    assert data["unreached"] == []
