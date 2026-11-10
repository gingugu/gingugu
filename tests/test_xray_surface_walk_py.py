"""X7d spec, Python walk: entry records and built-SQL records (``surface_records``).

The walk adds ``entries`` and ``sql`` to ``symbols_walk.walk``'s output, and
``symbols_walk.valid`` checks them through ``surface_records.valid(raw, "python")``.
"""

from __future__ import annotations

import ast
import json

from gingugu.xray import surface_records, symbols_walk
from gingugu.xray.lang import python


def _walk(src: str) -> dict:
    return symbols_walk.walk(ast.parse(src))


def test_the_walk_has_both_keys_and_round_trips() -> None:
    raw = _walk("x = 1\n")
    assert raw["entries"] == [] and raw["sql"] == []
    assert symbols_walk.valid(json.loads(json.dumps(raw)))
    assert python.ADAPTER.valid(json.loads(json.dumps(python.ADAPTER.parse("a.py", b"x = 1\n"))))


def test_a_decorated_function_is_an_entry_on_the_decorator_line() -> None:
    src = "@app.get('/x')\nasync def items():\n    pass\n\n@router.post\ndef make():\n    pass\n"
    assert _walk(src)["entries"] == [
        ["<module>", "app.get", "d", "items", "", 1, 0, 0, "GET", "/x"],
        ["<module>", "router.post", "d", "make", "", 5, 0, 0, "POST", ""],
    ]


def test_a_nested_handler_keeps_its_qual_and_its_scope() -> None:
    src = (
        "def register(mcp, vault):\n"  # 1
        "    @mcp.tool()\n"  # 2
        "    def credential_get(service):\n"  # 3
        "        return vault.get(service)\n"  # 4
    )
    assert _walk(src)["entries"] == [
        ["register", "mcp.tool", "d", "register.credential_get", "", 2, 0, 0, "", "credential_get"]
    ]


def test_a_method_handler_and_a_class_decorator() -> None:
    src = (
        "class Api:\n"  # 1
        "    @cli.command()\n"  # 2
        "    def run(self):\n"  # 3
        "        pass\n"  # 4
    )
    assert _walk(src)["entries"] == [["Api", "cli.command", "d", "Api.run", "", 2, 0, 0, "", "run"]]


def test_decorators_that_are_not_entries_are_ignored() -> None:
    src = (
        "@property\ndef a(): pass\n"
        "@functools.wraps(f)\ndef b(): pass\n"
        "@get\ndef c(): pass\n"  # a bare verb is anything
        "@x.y.z()\ndef d(): pass\n"
        "@make()()\ndef e(): pass\n"  # not a dotted name
    )
    assert _walk(src)["entries"] == []


def test_a_bare_shared_task_is_an_entry() -> None:
    assert _walk("@shared_task\ndef job(): pass\n")["entries"] == [
        ["<module>", "shared_task", "d", "job", "", 1, 0, 0, "", ""]
    ]


def test_registrars_name_the_first_name_shaped_argument() -> None:
    src = (
        "app.add_url_rule('/x', 'index', index)\n"  # 1: the string is skipped
        "urlpatterns = [path('a/', views.show), path('b/', include('x'))]\n"  # 2
        "def setup(sub):\n"  # 3
        "    p = sub.add_parser('run')\n"  # 4
        "    p.set_defaults(func=cmd_run)\n"  # 5: a keyword argument counts too
    )
    assert _walk(src)["entries"] == [
        ["<module>", "app.add_url_rule", "n", "index", "", 1, 0, 0, "GET", "/x"],
        ["<module>", "path", "a", "views", "show", 2, 0, 0, "", "a/"],
        ["setup", "p.set_defaults", "n", "cmd_run", "", 5, 0, 0, "", ""],
    ]


def test_a_registrar_given_self_method_is_form_s() -> None:
    src = (
        "class Server:\n"  # 1
        "    def setup(self):\n"  # 2
        "        self.app.add_url_rule('/x', view_func=self.index)\n"  # 3: a dotted chain
        "        app.add_api_route('/y', self.index)\n"  # 4
    )
    assert _walk(src)["entries"] == [
        ["Server.setup", "self.app.add_url_rule", "s", "Server", "index", 3, 0, 0, "GET", "/x"],
        ["Server.setup", "app.add_api_route", "s", "Server", "index", 4, 0, 0, "GET", "/y"],
    ]


def _sql(src: str) -> list:
    return _walk(src)["sql"]


def test_a_query_built_from_a_value_is_flagged() -> None:
    src = (
        "def q(cur, uid, name, col):\n"  # 1
        "    cur.execute(f'SELECT * FROM t WHERE id = {uid}')\n"  # 2
        "    cur.execute('SELECT * FROM t WHERE n = %s' % name)\n"  # 3
        "    cur.execute('SELECT ' + col + ' FROM t')\n"  # 4
        "    cur.executemany('SELECT {} FROM t'.format(col), rows)\n"  # 5
    )
    assert _sql(src) == [["q", 2], ["q", 3], ["q", 4], ["q", 5]]


def test_safe_shapes_are_not_flagged() -> None:
    src = (
        "TABLE = 'memories'\n"
        "def q(cur, ids, sql, cfg):\n"
        "    cur.execute('SELECT 1')\n"  # a literal
        "    cur.execute(sql)\n"  # a parameter: opaque, never guessed at
        "    cur.execute(f'SELECT * FROM {TABLE}')\n"  # an UPPER_CASE constant
        "    cur.execute(f'SELECT * FROM {cfg.TABLE}')\n"  # an UPPER_CASE attribute
        "    cur.execute(f\"SELECT * FROM t WHERE id IN ({','.join('?' * len(ids))})\", ids)\n"
        "    marks = ', '.join(['?'] * len(ids))\n"
        "    cur.execute(f'SELECT * FROM t WHERE id IN ({marks})', ids)\n"  # a safe local
        "    clause = 'AND x = ?' if ids else ''\n"
        "    cur.execute(f'SELECT * FROM t WHERE 1 {clause}')\n"  # both branches literal
        "    cur.run(f'SELECT {ids}')\n"  # not a query method
    )
    assert _sql(src) == []


def test_a_local_built_unsafely_is_flagged_where_it_runs() -> None:
    src = (
        "def q(cur, col):\n"  # 1
        "    sql = 'SELECT ' + col\n"  # 2
        "    cur.execute(sql)\n"  # 3
        "    where = ''\n"  # 4
        "    where = f'WHERE {col}'\n"  # 5: one unsafe assignment is enough
        "    cur.execute(f'SELECT * FROM t {where}')\n"  # 6
    )
    assert _sql(src) == [["q", 3], ["q", 6]]


def test_module_level_and_text_calls() -> None:
    src = "import sqlalchemy as sa\nstmt = sa.text(f'SELECT {x}')\n"
    assert _sql(src) == [["<module>", 2]]


def test_the_validator_refuses_foreign_shapes() -> None:
    good = _walk("@app.get('/x')\ndef a(): pass\n")
    assert symbols_walk.valid(good)
    for bad_entry in (
        ["<module>", "app.nope", "d", "a", "", 1, 0, 0],  # not a catalogued label
        ["<module>", "app.get", "d", "a\nb", "", 1, 0, 0],  # not an identifier
        ["<module>", "app.get", "i", "", "", 1, 0, 0],  # an inline span must be real
        ["<module>", "app.get", "d", "a", "", 1, 2, 3],  # only "i" carries a span
        ["<module>", "app.get", "d", "a", "", True, 0, 0],
    ):
        assert not symbols_walk.valid({**good, "entries": [bad_entry]}), bad_entry
    assert not symbols_walk.valid({**good, "sql": [["<module>", "1"]]})
    assert not surface_records.valid({"entries": [], "sql": None}, "python")


def test_an_http_registrar_takes_its_handler_after_the_path() -> None:
    src = (
        "app.add_route(DERIVE_PATH, derive, methods=['POST'])\n"  # 1: a constant path
        "target = path(state_dir)\n"  # 2: a bare `path` without a literal route
        "urlpatterns = [path('a/', show)]\n"  # 3
        "mcp.add_tool(handler)\n"  # 4: no path: the first argument is the handler
    )
    assert _walk(src)["entries"] == [
        ["<module>", "app.add_route", "n", "derive", "", 1, 0, 0, "POST", ""],
        ["<module>", "path", "n", "show", "", 3, 0, 0, "", "a/"],
        ["<module>", "mcp.add_tool", "n", "handler", "", 4, 0, 0, "", "handler"],
    ]


def test_a_main_guard_is_an_inline_entry_over_its_block() -> None:
    src = (
        "import sys\n"  # 1
        "def main(): pass\n"  # 2
        "if __name__ == '__main__':\n"  # 3
        "    sys.exit(main())\n"  # 4
        "if '__main__' == __name__:\n"  # 5
        "    main()\n"  # 6
        "if __name__ != '__main__':\n"  # 7: not a guard
        "    pass\n"  # 8
    )
    assert _walk(src)["entries"] == [
        ["<module>", "__main__", "i", "", "", 3, 3, 4, "", ""],
        ["<module>", "__main__", "i", "", "", 5, 5, 6, "", ""],
    ]
