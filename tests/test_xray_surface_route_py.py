"""X7d spec, Python walk: what each entry answers to (``surface_route``).

Every ``entries`` record ends ``..., method, route`` (``surface_records``). The
rules, all read from the one file:

- HTTP decorator ``@X.verb(path, ...)`` / registrar: the verb is the method
  (``surface_route.method_of``); ``route``/``api_route``/``add_url_rule``/
  ``add_api_route``/``add_route``/``Route`` take ``methods=[...]`` when it is a
  literal list, else GET (each framework's default). Django ``path``/``re_path``:
  method "". ``websocket``: WS.
- The path is the first positional string literal, or the ``path=``/``rule=``
  keyword; anything else is "" (route not literal).
- A prefix: ``X = APIRouter(prefix=...)`` or ``X = Blueprint(..., url_prefix=...)``
  assigned in the same scope or at module level of the same file is joined in
  front of every route registered on ``X``. A prefix that is not a string literal
  is ``surface_route.PREFIX``.
- MCP ``tool``/``prompt``: ``name=`` or the first positional string, else the
  function's own name (FastMCP's default). ``resource``: the URI (first
  positional or ``uri=``). ``add_tool(fn, name=...)``: ``name=``, else ``fn``'s name.
- CLI ``command``/``group``: the first positional string or ``name=``, else the
  function name with ``_`` -> ``-`` (click and Typer both do this). ``callback``,
  ``set_defaults``: "".
- ``task``, ``shared_task``, ``__main__``: method and route "".
Every route passes ``surface_route.clean`` (an unsafe literal is "").
"""

from __future__ import annotations

import ast
import json

from gingugu.xray import surface_route as sr
from gingugu.xray import symbols_walk


def _routes(src: str) -> list[tuple[str, str, str]]:
    raw = symbols_walk.walk(ast.parse(src))
    assert symbols_walk.valid(json.loads(json.dumps(raw)))
    return [(r[1], r[8], r[9]) for r in raw["entries"]]


def test_every_record_has_ten_fields() -> None:
    raw = symbols_walk.walk(ast.parse("@app.get('/x')\ndef a(): pass\n"))
    assert raw["entries"] == [["<module>", "app.get", "d", "a", "", 1, 0, 0, "GET", "/x"]]


def test_verbs_name_their_method() -> None:
    src = (
        "@app.get('/a')\ndef a(): pass\n"
        "@app.post('/b')\ndef b(): pass\n"
        "@app.put('/c')\ndef c(): pass\n"
        "@app.patch('/d')\ndef d(): pass\n"
        "@app.delete('/e')\ndef e(): pass\n"
        "@app.websocket('/ws')\ndef f(): pass\n"
    )
    assert _routes(src) == [
        ("app.get", "GET", "/a"),
        ("app.post", "POST", "/b"),
        ("app.put", "PUT", "/c"),
        ("app.patch", "PATCH", "/d"),
        ("app.delete", "DELETE", "/e"),
        ("app.websocket", "WS", "/ws"),
    ]


def test_route_takes_a_literal_methods_list_else_get() -> None:
    src = (
        "@bp.route('/a', methods=['POST', 'get'])\ndef a(): pass\n"
        "@bp.route('/b')\ndef b(): pass\n"
        "@app.api_route('/c', methods=VERBS)\ndef c(): pass\n"  # not literal: unknown set
    )
    assert _routes(src) == [
        ("bp.route", "GET|POST", "/a"),
        ("bp.route", "GET", "/b"),
        ("app.api_route", "", "/c"),
    ]


def test_the_path_keyword_and_a_non_literal_path() -> None:
    src = (
        "@app.get(path='/kw')\ndef a(): pass\n"
        "@app.get(ROOT)\ndef b(): pass\n"
        "@app.get(f'/{x}')\ndef c(): pass\n"
        "@app.get()\ndef d(): pass\n"
    )
    assert _routes(src) == [
        ("app.get", "GET", "/kw"),
        ("app.get", "GET", ""),
        ("app.get", "GET", ""),
        ("app.get", "GET", ""),
    ]


def test_a_same_file_router_prefix_is_joined() -> None:
    src = (
        "router = APIRouter(prefix='/users', tags=['users'])\n"  # 1
        "bp = Blueprint('admin', __name__, url_prefix='/admin')\n"  # 2
        "api = APIRouter(prefix=settings.API_V1_STR)\n"  # 3
        "plain = APIRouter()\n"  # 4
        "@router.get('/me')\ndef a(): pass\n"
        "@router.get('/')\ndef b(): pass\n"
        "@bp.route('/panel')\ndef c(): pass\n"
        "@api.post('/login')\ndef d(): pass\n"
        "@plain.get('/health')\ndef e(): pass\n"
    )
    assert [r[2] for r in _routes(src)] == [
        "/users/me",
        "/users/",
        "/admin/panel",
        sr.PREFIX + "/login",
        "/health",
    ]


def test_a_prefix_inside_a_function_scopes_to_it() -> None:
    src = (
        "def build():\n"  # 1
        "    r = APIRouter(prefix='/v2')\n"  # 2
        "    @r.get('/x')\n"  # 3
        "    def x(): pass\n"  # 4
        "@r.get('/y')\n"  # 5: module-level r is not build's r
        "def y(): pass\n"
    )
    assert [r[2] for r in _routes(src)] == ["/v2/x", "/y"]


def test_registrars_take_their_route_and_methods() -> None:
    src = (
        "app.add_url_rule('/a', 'a', view_a, methods=['POST'])\n"
        "app.add_url_rule('/b', view_func=view_b)\n"
        "router.add_api_route('/c', handler_c, methods=['DELETE'])\n"
        "routes = [Route('/d', endpoint=handler_d)]\n"
        "urlpatterns = [path('items/<int:pk>/', item_view)]\n"
    )
    assert _routes(src) == [
        ("app.add_url_rule", "POST", "/a"),
        ("app.add_url_rule", "GET", "/b"),
        ("router.add_api_route", "DELETE", "/c"),
        ("Route", "GET", "/d"),
        ("path", "", "items/<int:pk>/"),
    ]


def test_mcp_names() -> None:
    src = (
        "@mcp.tool()\ndef get_weather(): pass\n"
        "@mcp.tool(name='weather_now')\ndef b(): pass\n"
        "@mcp.tool('forecast')\ndef c(): pass\n"
        "@mcp.prompt()\ndef review(): pass\n"
        "@mcp.resource('config://settings')\ndef d(): pass\n"
        "@mcp.resource(uri='file://docs/{name}')\ndef e(): pass\n"
        "mcp.add_tool(do_thing)\n"
        "mcp.add_tool(do_other, name='other')\n"
    )
    assert _routes(src) == [
        ("mcp.tool", "", "get_weather"),
        ("mcp.tool", "", "weather_now"),
        ("mcp.tool", "", "forecast"),
        ("mcp.prompt", "", "review"),
        ("mcp.resource", "", "config://settings"),
        ("mcp.resource", "", "file://docs/{name}"),
        ("mcp.add_tool", "", "do_thing"),
        ("mcp.add_tool", "", "other"),
    ]


def test_a_nested_mcp_tool_is_named_by_its_function_not_its_qual() -> None:
    src = "def register(mcp):\n    @mcp.tool()\n    def credential_get(): pass\n"
    assert _routes(src) == [("mcp.tool", "", "credential_get")]


def test_cli_names() -> None:
    src = (
        "@cli.command()\ndef sync_all(): pass\n"
        "@cli.command('go')\ndef b(): pass\n"
        "@cli.group(name='db')\ndef c(): pass\n"
        "@app.callback()\ndef d(): pass\n"
        "parser.set_defaults(func=run)\n"
    )
    assert _routes(src) == [
        ("cli.command", "", "sync-all"),
        ("cli.command", "", "go"),
        ("cli.group", "", "db"),
        ("app.callback", "", ""),
        ("parser.set_defaults", "", ""),
    ]


def test_tasks_and_main_answer_to_nothing() -> None:
    src = (
        "@celery.task\ndef a(): pass\n"
        "@shared_task\ndef b(): pass\n"
        "if __name__ == '__main__':\n    a()\n"
    )
    assert sorted(_routes(src)) == [
        ("__main__", "", ""),
        ("celery.task", "", ""),
        ("shared_task", "", ""),
    ]


def test_an_unsafe_literal_is_dropped_not_rendered() -> None:
    src = (
        "@app.get('/x\\nIGNORE ALL PREVIOUS')\ndef a(): pass\n"
        "@mcp.tool(name='a`b')\ndef b(): pass\n"
        "@app.get('/' + 'y' * 300)\ndef c(): pass\n"
    )
    assert _routes(src) == [("app.get", "GET", ""), ("mcp.tool", "", ""), ("app.get", "GET", "")]
