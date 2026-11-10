"""X7d: the linkers keep catalogued sinks and entries; the map walks entry -> sink.

Each language's linker hands every call verdict to ``surface_link.Collector``:
an external call whose dotted name the catalogue lists is a sink, nothing else
external is stored; an entry record becomes an ``Entry`` with the nodes its
reach starts from. ``surface.build`` then walks the call edges forward.
"""

from __future__ import annotations

import json

from gingugu.xray import surface, symbols
from gingugu.xray.lang import golang, python, typescript
from gingugu.xray.symbols_model import Entry, Symbols

GOMOD = {"go.mod": b"module example.com/app\n\ngo 1.22\n"}


def _py(files: dict[str, str]) -> Symbols:
    return python.ADAPTER.extract({k: v.encode() for k, v in files.items()}, {}).symbols


def _ts(files: dict[str, str]) -> Symbols:
    return typescript.ADAPTER.extract({k: v.encode() for k, v in files.items()}, {}).symbols


def _go(files: dict[str, str]) -> Symbols:
    return golang.ADAPTER.extract({k: v.encode() for k, v in files.items()}, GOMOD).symbols


# --- sinks --------------------------------------------------------------------------


def test_python_sinks_by_import_binding_and_builtin() -> None:
    src = (
        "import subprocess\n"  # 1
        "from os import system\n"  # 2
        "import pickle as pk\n"  # 3
        "def run(c): subprocess.run(c)\n"  # 4
        "def s(c): system(c)\n"  # 5
        "def l(b): return pk.loads(b)\n"  # 6
        "def o(p): open(p, 'w')\n"  # 7
        "def ok(x): len(x); subprocess.list2cmdline(x)\n"  # 8: external, not catalogued
    )
    assert _py({"a.py": src}).sinks == [
        ("a.py", "run", "subprocess.run", 4),
        ("a.py", "s", "os.system", 5),
        ("a.py", "l", "pickle.loads", 6),
        ("a.py", "o", "open", 7),
    ]


def test_python_a_repo_module_is_never_a_sink() -> None:
    files = {
        "subprocess.py": "def run(c): pass\n",
        "a.py": "import subprocess\nsubprocess.run(1)\n",
    }
    assert _py(files).sinks == []


def test_ts_sinks_by_specifier_and_global() -> None:
    src = (
        'import { exec } from "child_process";\n'  # 1
        'import fs from "node:fs";\n'  # 2
        'import * as cp from "child_process";\n'  # 3
        "export function a(c) {\n"  # 4
        "  exec(c);\n"  # 5
        '  fs.writeFileSync(c, "");\n'  # 6
        "  cp.spawn(c);\n"  # 7
        "  eval(c);\n"  # 8
        "  fetch(c);\n"  # 9
        "}\n"  # 10
        "function b(exec) { exec(); }\n"  # 11: a parameter shadows the import
    )
    assert [(s[1], s[2], s[3]) for s in _ts({"a.ts": src}).sinks] == [
        ("a", "child_process.exec", 5),
        ("a", "fs.writeFileSync", 6),
        ("a", "child_process.spawn", 7),
        ("a", "eval", 8),
        ("a", "fetch", 9),
    ]


def test_go_sinks_by_import_path() -> None:
    src = (
        "package main\n"  # 1
        "\n"  # 2
        'import (\n\t"os"\n\t"os/exec"\n)\n'  # 3-6
        "\n"  # 7
        "func run(c string) {\n"  # 8
        "\texec.Command(c).Run()\n"  # 9
        "\tos.Remove(c)\n"  # 10
        "\tos.Getenv(c)\n"  # 11: external, not catalogued
        "}\n"  # 12
    )
    assert _go({"main.go": src}).sinks == [
        ("main.go", "run", "os/exec.Command", 9),
        ("main.go", "run", "os.Remove", 10),
    ]


# --- entries and reach --------------------------------------------------------------

JOBS = (
    'import { exec } from "child_process";\n'
    "export function run(c) { spawnIt(c); }\n"
    "function spawnIt(c) { exec(c); }\n"
)
API = (
    'import { run } from "./jobs";\n'  # 1
    'app.get("/x", list);\n'  # 2
    'app.post("/y", (req) => {\n'  # 3
    "  run(req.body);\n"  # 4
    "  eval(req.q);\n"  # 5
    "});\n"  # 6
    "function list() { return 1; }\n"  # 7
)


def test_ts_entries_named_and_inline() -> None:
    sym = _ts({"jobs.ts": JOBS, "api.ts": API})
    by_line = {e.line: e for e in sym.entries}
    assert by_line[2] == Entry(
        "api.ts", "app.get", "http", 2, "list", (("api.ts", "list"),), method="GET", route="/x"
    )
    inline = by_line[3]
    assert inline.inline and inline.handler == "<module>"
    assert (inline.method, inline.route) == ("POST", "/y")
    assert inline.nodes == (("jobs.ts", "run"),)
    assert inline.direct == (("eval", 5),)


def test_the_map_walks_from_an_entry_to_every_sink() -> None:
    data = surface.build(_ts({"jobs.ts": JOBS, "api.ts": API}))
    rows = {e.line: found for e, found in data["entries"]}
    assert rows[2] == []
    assert [(r.sink, r.category, r.symbol, r.hops, r.via) for r in rows[3]] == [
        ("child_process.exec", "exec", "spawnIt", 2, ("run", "spawnIt")),
        ("eval", "eval", "<module>", 0, ()),
    ]
    assert data["unreached"] == [] and data["sinks"] == 2
    assert [e.line for e, _ in data["entries"]] == [3, 2]  # reaching exec sorts first


def test_go_main_and_a_method_handler_reach_exec() -> None:
    src = (
        "package main\n"  # 1
        "\n"  # 2
        'import (\n\t"net/http"\n\t"os/exec"\n)\n'  # 3-6
        "\n"  # 7
        "type Server struct{}\n"  # 8
        "\n"  # 9
        "func (s *Server) handle(w http.ResponseWriter, r *http.Request) {\n"  # 10
        '\texec.Command("ls")\n'  # 11
        "}\n"  # 12
        "\n"  # 13
        "func main() {\n"  # 14
        "\ts := &Server{}\n"  # 15
        '\thttp.HandleFunc("/x", s.handle)\n'  # 16
        "}\n"  # 17
    )
    data = surface.build(_go({"main.go": src}))
    rows = {(e.label, e.handler): found for e, found in data["entries"]}
    assert [(r.sink, r.hops) for r in rows[("http.HandleFunc", "Server.handle")]] == [
        ("os/exec.Command", 0)
    ]
    routes = {e.handler: (e.method, e.route) for e, _ in data["entries"]}
    assert routes == {"Server.handle": ("", "/x"), "main": ("", "")}
    assert [(r.sink, r.hops, r.via) for r in rows[("main", "main")]] == []  # a value, not a call


def test_an_unresolved_handler_is_kept_and_reaches_nothing() -> None:
    sym = _ts({"api.ts": 'import { h } from "lib";\napp.get("/x", h);\n'})
    (e,) = sym.entries
    assert (e.handler, e.nodes) == ("h", ())
    assert surface.build(sym)["entries"] == [(e, [])]


def test_db_sinks_join_table_uses_to_their_definition() -> None:
    sym = Symbols(defs={"a.py": {"f": ("function", 1, 5)}})
    uses = {"users": [("a.py", 3), ("a.py", 9)]}
    assert surface.db_sinks(sym, uses) == [
        ("a.py", "f", "db:users", 3),
        ("a.py", "<module>", "db:users", 9),
    ]
    assert surface.category("db:users") == "db"


def test_skip_drops_test_entries_and_sinks() -> None:
    sym = _ts(
        {
            "a.test.ts": 'import { exec } from "child_process";\napp.get("/t", () => exec(1));\n',
            "b.ts": 'import { exec } from "child_process";\nexport function f() { exec(1); }\n',
        }
    )
    data = surface.build(sym, skip=lambda p: p.endswith(".test.ts"))
    assert data["entries"] == []
    assert [(s[0], s[2]) for s in data["unreached"]] == [("b.ts", "child_process.exec")]


# --- the cache form -----------------------------------------------------------------


def test_sinks_and_entries_survive_the_cache_round_trip() -> None:
    sym = _ts({"jobs.ts": JOBS, "api.ts": API})
    back = symbols.from_json(json.loads(json.dumps(symbols.to_json(sym))))
    assert back == sym and back.entries and back.sinks


def test_the_cache_refuses_a_name_the_catalogue_never_wrote() -> None:
    good = symbols.to_json(_ts({"jobs.ts": JOBS, "api.ts": API}))
    assert symbols.from_json(good) is not None
    sink = good["sinks"][0]
    entry = good["entries"][0]
    for bad in (
        {**good, "sinks": [[sink[0], sink[1], "IGNORE PREVIOUS", sink[3]]]},
        {**good, "sinks": [[sink[0], "a\nb", sink[2], sink[3]]]},
        {**good, "entries": [[*entry[:4], "h\nINJECTED", *entry[5:]]]},
        {**good, "entries": [[entry[0], "app get", *entry[2:]]]},
        {**good, "entries": [[*entry[:2], "rce", *entry[3:]]]},
        {**good, "entries": [[*entry[:7], "yes", *entry[8:]]]},
        {**good, "entries": [["/etc/passwd", *entry[1:]]]},
        {**good, "entries": [[*entry[:8], "get", entry[9]]]},  # a method not in METHODS
        {**good, "entries": [[*entry[:8], "POST|GET", entry[9]]]},  # out of order
        {**good, "entries": [[*entry[:9], "/x\nIGNORE PREVIOUS"]]},
        {**good, "entries": [[*entry[:9], "/`x`"]]},
        {**good, "entries": [[*entry[:9], "/" + "x" * 300]]},
        {**good, "entries": [entry[:8]]},  # the pre-route shape
    ):
        assert symbols.from_json(bad) is None, bad


def test_a_main_guard_reaches_what_the_script_runs() -> None:
    src = (
        "import subprocess\n"  # 1
        "def main():\n"  # 2
        "    subprocess.run(['ls'])\n"  # 3
        "if __name__ == '__main__':\n"  # 4
        "    main()\n"  # 5
    )
    data = surface.build(_py({"tool.py": src}))
    ((e, found),) = data["entries"]
    assert (e.kind, e.label, e.inline, e.nodes) == (
        "main",
        "__main__",
        True,
        (("tool.py", "main"),),
    )
    assert [(r.sink, r.hops, r.via) for r in found] == [("subprocess.run", 1, ("main",))]


def test_a_pyproject_script_function_is_a_cli_entry() -> None:
    files = {"pkg/__init__.py": b"", "pkg/cli.py": b"import os\ndef main():\n    os.system('x')\n"}
    manifests = {
        "pyproject.toml": b'[project.scripts]\ntool = "pkg.cli:main"\nbad = "pkg.nope:x"\n'
    }
    sym = python.ADAPTER.extract(files, manifests).symbols
    (e,) = sym.entries
    assert (e.path, e.label, e.kind, e.line, e.handler, e.route) == (
        "pkg/cli.py",
        "project.scripts",
        "cli",
        2,
        "main",
        "tool",
    )
    assert [(r.sink, r.hops) for _, f in surface.build(sym)["entries"] for r in f] == [
        ("os.system", 0)
    ]


def test_each_script_name_is_its_own_cli_entry() -> None:
    files = {"pkg/__init__.py": b"", "pkg/cli.py": b"def main():\n    pass\n"}
    manifests = {
        "pyproject.toml": (
            b'[project.scripts]\ntool = "pkg.cli:main"\nt = "pkg.cli:main"\n'
            b'"bad`name" = "pkg.cli:main"\n'
        )
    }
    sym = python.ADAPTER.extract(files, manifests).symbols
    assert sorted(e.route for e in sym.entries) == ["", "t", "tool"]  # an unsafe name: ""
