"""X7d against a hostile checkout: crafted files cost linear time and never a language.

A file in a repo under review is untrusted. The SQL safe-part checks follow local
aliases, the map walks call chains and the cache is read back into the agent's
context, so each has a crafted input that must stay cheap and contained.
"""

from __future__ import annotations

import json
import time

from gingugu.xray import surface, symbols
from gingugu.xray.lang import golang, typescript
from gingugu.xray.symbols_model import Entry, Symbols

LEVELS = 40  # 2**40 steps if a doubling alias chain is judged per path


def _timed(fn):
    start = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - start


def test_a_doubling_alias_chain_in_ts_is_judged_once_per_name() -> None:
    lines = ["function f(db, x) {", "  const x0 = `${x}`;"]
    lines += [f"  const x{i} = x{i - 1} + x{i - 1};" for i in range(1, LEVELS)]
    lines += [f"  db.query(x{LEVELS - 1});", "}"]
    raw, took = _timed(lambda: typescript.ADAPTER.parse("a.ts", "\n".join(lines).encode()))
    assert raw["sym"]["sql"] == [["f", LEVELS + 2]]
    assert took < 5


def test_a_doubling_alias_chain_in_go_is_judged_once_per_name() -> None:
    lines = ["package p", "", "func f(db *sql.DB, x string) {", '\tx0 := "a" + x']
    lines += [f"\tx{i} := x{i - 1} + x{i - 1}" for i in range(1, LEVELS)]
    lines += [f"\tdb.Query(x{LEVELS - 1})", "}"]
    raw, took = _timed(lambda: golang.ADAPTER.parse("a.go", "\n".join(lines).encode()))
    assert raw["sym"]["sql"] == [["f", LEVELS + 4]]
    assert took < 5


def test_a_long_concatenation_in_go_keeps_the_language() -> None:
    terms = " + ".join(f"v{i}" for i in range(3000))
    src = f'package p\n\nfunc f(db *sql.DB) {{\n\tdb.Query("SELECT " + {terms})\n}}\n'
    raw = golang.ADAPTER.parse("a.go", src.encode())
    assert golang.ADAPTER.valid(raw)
    assert raw["sym"]["sql"] == [["f", 4]]
    assert [d[0] for d in raw["sym"]["defs"]] == ["f"]


def test_a_deep_call_chain_keeps_only_a_short_path_per_sink() -> None:
    n = 20000
    edges = {("a.py", f"f{i}", "a.py", f"f{i + 1}"): 1 for i in range(n)}
    sinks = [("a.py", f"f{i}", "os.system", i + 1) for i in range(n + 1)]
    entry = Entry("a.py", "app.get", "http", 1, "f0", (("a.py", "f0"),))
    sym = Symbols(edges=edges, sinks=sinks, entries=[entry])
    data, took = _timed(lambda: surface.build(sym))
    ((_, found),) = data["entries"]
    assert len(found) == n + 1
    deepest = max(found, key=lambda r: r.hops)
    assert deepest.hops == n and len(deepest.via) == surface.MAX_VIA and deepest.cut
    assert not min(found, key=lambda r: r.hops).cut
    assert took < 10


def test_an_inline_entry_marks_a_cut_path_exactly() -> None:
    edges = {("a.ts", f"f{i}", "a.ts", f"f{i + 1}"): 1 for i in range(10)}
    at = surface.MAX_VIA  # the callee MAX_VIA calls in: its path shows exactly MAX_VIA names
    sinks = [("a.ts", f"f{at - 1}", "eval", 1), ("a.ts", f"f{at}", "eval", 2)]
    entry = Entry("a.ts", "app.get", "http", 1, "<module>", (("a.ts", "f0"),), inline=True)
    found = surface.build(Symbols(edges=edges, sinks=sinks, entries=[entry]))["entries"][0][1]
    by_line = {r.line: r for r in found}
    assert (by_line[1].hops, len(by_line[1].via), by_line[1].cut) == (at, at, False)
    assert (by_line[2].hops, len(by_line[2].via), by_line[2].cut) == (at + 1, at, True)


def test_the_cache_bounds_lines_and_names() -> None:
    good = symbols.to_json(
        Symbols(
            defs={"a.py": {"f": ("function", 1, 2)}},
            sinks=[("a.py", "f", "os.system", 1)],
            entries=[Entry("a.py", "app.get", "http", 1, "f", (("a.py", "f"),))],
        )
    )
    assert symbols.from_json(json.loads(json.dumps(good))) is not None
    long = "f" * 513
    for bad in (
        {**good, "sinks": [["a.py", "f", "os.system", 10**12]]},
        {**good, "sinks": [["a.py", long, "os.system", 1]]},
        {**good, "entries": [["a.py", "app.get", "http", 10**12, "f", [], [], False, "", ""]]},
        {**good, "entries": [["a.py", "app.get", "http", 1, long, [], [], False, "", ""]]},
        {**good, "entries": [["a.py", "app.get", "http", 1, "f", [], [], False, "", "/" * 257]]},
        {**good, "entries": [["a.py", "app.get", "http", 1, "f", [], [], False, "GET" * 99, ""]]},
        {**good, "defs": {"a.py": {long: ["function", 1, 2]}}},
    ):
        assert symbols.from_json(bad) is None
