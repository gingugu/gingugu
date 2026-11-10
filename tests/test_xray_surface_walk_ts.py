"""X7d spec, TS/JS walk: entry records and built-SQL records (``surface_records``).

The walk adds ``entries`` and ``sql`` to ``ts_symbols_walk.walk``'s output, and
``ts_symbols_walk.valid`` checks them through
``surface_records.valid(raw, "typescript")``. A label is the callee as a dotted
chain of identifiers (``this`` included); a handler is every function-shaped
argument after the first (inline, form "i", its own line span) plus the last
argument when it is name-shaped (``h`` -> "n", ``ctrl.create`` -> "a",
``this.h`` in a class -> "s"). A route call's first argument must be a string or
template literal - ``app.get("port")`` and ``client.get(url, cfg)`` are not routes.
"""

from __future__ import annotations

import json

from gingugu.xray import surface_records
from gingugu.xray.lang import ts_symbols_walk, typescript

A = typescript.ADAPTER


def _walk(src: str, name: str = "a.ts") -> dict:
    return A.parse(name, src.encode())["sym"]


def test_the_walk_has_both_keys_and_round_trips() -> None:
    raw = A.parse("a.ts", b"const x = 1;\n")
    assert raw["sym"]["entries"] == [] and raw["sym"]["sql"] == []
    assert A.valid(json.loads(json.dumps(raw)))


def test_a_named_handler_and_a_member_handler() -> None:
    src = (
        'app.get("/x", handler);\n'  # 1
        'router.post("/y", auth, ctrl.create);\n'  # 2: middleware is not the handler
        "app.get(`/z`, list);\n"  # 3: a template literal path
    )
    assert _walk(src)["entries"] == [
        ["<module>", "app.get", "n", "handler", "", 1, 0, 0],
        ["<module>", "router.post", "a", "ctrl", "create", 2, 0, 0],
        ["<module>", "app.get", "n", "list", "", 3, 0, 0],
    ]


def test_an_inline_handler_carries_its_span() -> None:
    src = (
        "function setup(app) {\n"  # 1
        '  app.get("/x", (req, res) => {\n'  # 2
        "    exec(req.query.cmd);\n"  # 3
        "  });\n"  # 4
        '  app.post("/y", async function (req, res) { save(req); });\n'  # 5
        "}\n"  # 6
    )
    assert _walk(src, "a.js")["entries"] == [
        ["setup", "app.get", "i", "", "", 2, 2, 4],
        ["setup", "app.post", "i", "", "", 5, 5, 5],
    ]


def test_mcp_tools_and_this_handlers() -> None:
    src = (
        "class Api {\n"  # 1
        "  wire() {\n"  # 2
        '    this.app.get("/x", this.list);\n'  # 3
        '    server.tool("echo", schema, async (args) => run(args));\n'  # 4
        '    srv.registerTool("ping", {}, ping);\n'  # 5
        "  }\n"  # 6
        "}\n"  # 7
    )
    assert _walk(src)["entries"] == [
        ["Api.wire", "this.app.get", "s", "Api", "list", 3, 0, 0],
        ["Api.wire", "server.tool", "i", "", "", 4, 4, 4],
        ["Api.wire", "srv.registerTool", "n", "ping", "", 5, 0, 0],
    ]


def test_calls_that_are_not_routes() -> None:
    src = (
        'app.get("port");\n'  # no handler
        "app.get(name, h);\n"  # the path is not a literal
        'client.get("/x", cfg);\n'  # not a server receiver
        'app.use("/x", h);\n'  # not a verb
        'app.get("/x", wrap(h));\n'  # the last argument is a call
    )
    assert _walk(src)["entries"] == []


def test_a_nest_decorator_is_an_entry() -> None:
    src = (
        "class C {\n"  # 1
        "  @Get()\n"  # 2
        "  list() { return 1; }\n"  # 3
        "  @Injectable()\n"  # 4
        "  other() {}\n"  # 5
        "}\n"  # 6
    )
    found = [e[1:] for e in _walk(src)["entries"]]  # the scope is the grammar's to place
    assert found == [["Get", "d", "C.list", "", 2, 0, 0]]


def _sql(src: str) -> list:
    return _walk(src)["sql"]


def test_a_query_built_from_a_value_is_flagged() -> None:
    src = (
        "function f(db, id, col) {\n"  # 1
        "  db.query(`SELECT * FROM t WHERE id = ${id}`);\n"  # 2
        '  db.query("SELECT " + col + " FROM t");\n'  # 3
        "  const sql = `SELECT ${col} FROM t`;\n"  # 4
        "  db.execute(sql);\n"  # 5: a local built unsafely
        "  prisma.$queryRawUnsafe(`SELECT ${col}`);\n"  # 6
        "}\n"
    )
    assert _sql(src) == [["f", 2], ["f", 3], ["f", 5], ["f", 6]]


def test_safe_shapes_are_not_flagged() -> None:
    src = (
        "const COLS = 'a, b';\n"
        "function f(db, ids, sql, cfg) {\n"
        '  db.query("SELECT 1");\n'
        "  db.query(sql);\n"  # a parameter: opaque
        "  db.query(`SELECT ${COLS} FROM t`);\n"  # an UPPER_CASE constant
        "  db.query(`SELECT ${cfg.TABLE} FROM t`);\n"  # an UPPER_CASE property
        '  db.query(`SELECT * FROM t WHERE id IN (${ids.map(() => "?").join(",")})`, ids);\n'
        "  const marks = ids.map(() => '?').join(', ');\n"
        "  db.query(`SELECT * FROM t WHERE id IN (${marks})`, ids);\n"  # a safe local
        "  db.query(sql`SELECT * FROM t WHERE id = ${ids[0]}`);\n"  # a tagged template
        "  log.info(`SELECT ${ids}`);\n"  # not a query method
        "}\n"
    )
    assert _sql(src) == []


def test_the_validator_refuses_foreign_shapes() -> None:
    good = _walk('app.get("/x", h);\n')
    assert ts_symbols_walk.valid(good)
    for bad in (
        ["<module>", "app.nope", "n", "h", "", 1, 0, 0],
        ["<module>", "app.get", "n", "h;x", "", 1, 0, 0],
        ["<module>", "app.get", "i", "", "", 1, 3, 2],
    ):
        assert not ts_symbols_walk.valid({**good, "entries": [bad]}), bad
    assert not ts_symbols_walk.valid({k: v for k, v in good.items() if k != "sql"})
    assert surface_records.valid(good, "typescript")
