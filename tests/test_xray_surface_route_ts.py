"""X7d spec, TS/JS walk: what each entry answers to (``surface_route``).

Every ``entries`` record ends ``..., method, route``. The rules, from one file:

- A route call ``recv.verb(path, ...)``: the verb is the method (``all`` is "");
  the path is the first argument when it is a string, or a template literal
  with no substitution; any other first argument keeps today's rule (not a
  route call at all).
- A chained route ``recv.route(path).verb(h)`` (Express ``app.route``/
  ``router.route``, chains of several verbs included) is an entry per verb, each
  with that path. Each verb's handlers follow the usual rule (inline "i", last
  name-shaped "n"/"a"/"s"); its label is ``recv.verb``.
- NestJS: ``@Controller(p)`` (a string, or ``{ path: p }``; none or ``()`` is
  "") on a class is the prefix of every ``@Get(q)``-style method in it; the
  route is ``"/" + join(p, q)`` with leading slashes collapsed to one (``@Get()``
  in ``@Controller('cats')`` -> ``/cats``; in a bare ``@Controller()`` -> ``/``).
  The decorator names the method (``@All`` -> "").
- MCP ``server.tool(name, ...)``, ``registerTool(name, ...)``,
  ``registerResource(name, uri, ...)``, ``registerPrompt(name, ...)``: method "",
  route the name.
Every route passes ``surface_route.clean``.
"""

from __future__ import annotations

import json

from gingugu.xray.lang import typescript

A = typescript.ADAPTER


def _routes(src: str) -> list[tuple[str, str, str, str]]:
    raw = A.parse("a.ts", src.encode())
    assert A.valid(json.loads(json.dumps(raw)))
    return [(r[1], r[2], r[8], r[9]) for r in raw["sym"]["entries"]]


def test_every_record_has_ten_fields() -> None:
    raw = A.parse("a.ts", b'app.get("/x", h);\n')["sym"]
    assert raw["entries"] == [["<module>", "app.get", "n", "h", "", 1, 0, 0, "GET", "/x"]]


def test_express_verbs() -> None:
    src = (
        "app.get('/a', a);\n"
        'router.post("/b", auth, ctrl.create);\n'
        "app.all('/c', c);\n"
        "app.delete(`/d/:id`, d);\n"
        "app.put(`/e/${v}`, e);\n"  # a template with a substitution: not literal
    )
    assert _routes(src) == [
        ("app.get", "n", "GET", "/a"),
        ("router.post", "a", "POST", "/b"),
        ("app.all", "n", "", "/c"),
        ("app.delete", "n", "DELETE", "/d/:id"),
        ("app.put", "n", "PUT", ""),
    ]


def test_an_inline_handler_carries_the_route_too() -> None:
    src = "app.post('/login', (req, res) => {\n  res.send(1);\n});\n"
    assert _routes(src) == [("app.post", "i", "POST", "/login")]


def test_a_chained_route() -> None:
    src = (
        "app.route('/book')\n"  # 1
        "  .get(list)\n"  # 2
        "  .post((req, res) => { save(req); });\n"  # 3
        "router.route('/one').delete(ctrl.remove);\n"  # 4
        "x.route('/no').get(y);\n"  # 5: not a server-shaped receiver
    )
    assert _routes(src) == [
        ("app.get", "n", "GET", "/book"),
        ("app.post", "i", "POST", "/book"),
        ("router.delete", "a", "DELETE", "/one"),
    ]


def test_nestjs_controller_prefix() -> None:
    src = (
        "@Controller('cats')\n"
        "export class CatsController {\n"
        "  @Get()\n  findAll() {}\n"
        "  @Get(':id')\n  findOne() {}\n"
        "  @Post('/bulk')\n  create() {}\n"
        "  @All('*')\n  any() {}\n"
        "}\n"
        "@Controller({ path: 'dogs', version: '1' })\n"
        "class Dogs {\n  @Delete(':id')\n  remove() {}\n}\n"
        "@Controller()\n"
        "class Root {\n  @Get()\n  index() {}\n  @Get('health')\n  health() {}\n}\n"
    )
    assert [(r[2], r[3]) for r in _routes(src)] == [
        ("GET", "/cats"),
        ("GET", "/cats/:id"),
        ("POST", "/cats/bulk"),
        ("", "/cats/*"),
        ("DELETE", "/dogs/:id"),
        ("GET", "/"),
        ("GET", "/health"),
    ]


def test_mcp_names() -> None:
    src = (
        "server.tool('add', schema, async (a) => a);\n"
        "mcpServer.registerTool('echo', cfg, handler);\n"
        "mcpServer.registerResource('docs', 'file://docs/{name}', cfg, read);\n"
        "mcpServer.registerPrompt('review', cfg, prompt);\n"
    )
    assert [(r[0], r[2], r[3]) for r in _routes(src)] == [
        ("server.tool", "", "add"),
        ("mcpServer.registerTool", "", "echo"),
        ("mcpServer.registerResource", "", "docs"),
        ("mcpServer.registerPrompt", "", "review"),
    ]


def test_an_unsafe_literal_is_dropped() -> None:
    src = "app.get('/x y', a);\nserver.tool('a\"b', s, h);\n"
    assert [(r[2], r[3]) for r in _routes(src)] == [("GET", ""), ("", "")]
