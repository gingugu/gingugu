"""X7d spec, Go walk: entry records and built-SQL records (``surface_records``).

The walk adds ``entries`` and ``sql`` to ``go_symbols_walk.walk``'s output, and
``go_symbols_walk.valid`` checks them through ``surface_records.valid(raw, "go")``.
A label is the callee's selector chain (``http.HandleFunc``, ``s.mux.HandleFunc``);
a handler is every function literal argument after the first (form "i", its own
line span) plus the last argument when it is name-shaped (``h`` -> "n",
``s.handleX`` -> "a"). ``func main`` in package ``main`` is an entry of label
"main", form "d", on its own line. The query of a ``*Context`` method is its
second argument; of every other query method, its first.
"""

from __future__ import annotations

import json

from gingugu.xray import surface_records
from gingugu.xray.lang import go_symbols_walk, golang

A = golang.ADAPTER


def _walk(src: str) -> dict:
    return A.parse("a.go", src.encode())["sym"]


def test_the_walk_has_both_keys_and_round_trips() -> None:
    raw = A.parse("a.go", b"package x\n")
    assert raw["sym"]["entries"] == [] and raw["sym"]["sql"] == []
    assert A.valid(json.loads(json.dumps(raw)))


def test_main_and_its_routes() -> None:
    src = (
        "package main\n"  # 1
        "\n"  # 2
        'import "net/http"\n'  # 3
        "\n"  # 4
        "func main() {\n"  # 5
        '\thttp.HandleFunc("/x", handle)\n'  # 6
        '\thttp.HandleFunc("/y", func(w http.ResponseWriter, r *http.Request) {\n'  # 7
        "\t\tsave(r)\n"  # 8
        "\t})\n"  # 9
        '\thttp.Handle("/z", wrap(handle))\n'  # 10: a wrapping call: the handler is found inside
        "}\n"  # 11
    )
    assert _walk(src)["entries"] == [
        ["<module>", "main", "d", "main", "", 5, 0, 0, "", "", ""],
        ["main", "http.HandleFunc", "n", "handle", "", 6, 0, 0, "", "/x", "http"],
        ["main", "http.HandleFunc", "i", "", "", 7, 7, 9, "", "/y", "http"],
        ["main", "http.Handle", "n", "handle", "", 10, 0, 0, "", "/z", "http"],
    ]


def test_main_outside_package_main_is_not_an_entry() -> None:
    assert _walk("package tool\n\nfunc main() {}\n")["entries"] == []


def test_method_routes_and_router_verbs() -> None:
    src = (
        "package api\n"  # 1
        "\n"  # 2
        "func (s *Server) routes() {\n"  # 3
        '\ts.mux.HandleFunc("/x", s.handleX)\n'  # 4
        '\ts.r.GET("/items", s.list)\n'  # 5
        "}\n"  # 6
        "\n"  # 7
        "func setup(r *gin.Engine, h *Handler) {\n"  # 8
        '\tr.POST("/items", h.Create)\n'  # 9
        '\tr.Use("/x", h.Log)\n'  # 10: not a verb
        "}\n"  # 11
    )
    assert _walk(src)["entries"] == [
        ["Server.routes", "s.mux.HandleFunc", "a", "s", "handleX", 4, 0, 0, "", "/x", ""],
        ["Server.routes", "s.r.GET", "a", "s", "list", 5, 0, 0, "GET", "/items", ""],
        ["setup", "r.POST", "a", "h", "Create", 9, 0, 0, "POST", "/items", "#0"],
    ]


def _sql(src: str) -> list:
    return _walk(src)["sql"]


def test_a_query_built_from_a_value_is_flagged() -> None:
    src = (
        "package store\n"  # 1
        "\n"  # 2
        "func q(db *sql.DB, ctx context.Context, id string, col string) {\n"  # 3
        '\tdb.Query("SELECT * FROM t WHERE id = " + id)\n'  # 4
        '\tdb.Exec(fmt.Sprintf("DELETE FROM %s", col))\n'  # 5
        '\tq := fmt.Sprintf("SELECT %s FROM t", col)\n'  # 6
        "\tdb.QueryRow(q)\n"  # 7: a local built unsafely
        '\tdb.QueryContext(ctx, "SELECT " + col)\n'  # 8: the second argument
        "}\n"  # 9
    )
    assert _sql(src) == [["q", 4], ["q", 5], ["q", 7], ["q", 8]]


def test_safe_shapes_are_not_flagged() -> None:
    src = (
        "package store\n"
        "\n"
        "func q(db *sql.DB, ctx context.Context, ids []any, query string) {\n"
        '\tdb.Query("SELECT * FROM t WHERE id = ?", ids[0])\n'
        "\tdb.Query(`SELECT 1`)\n"  # a raw string
        "\tdb.Query(query)\n"  # a parameter: opaque
        '\tmarks := strings.Repeat("?,", len(ids))\n'
        '\tdb.Query("SELECT * FROM t WHERE id IN (" + marks + ")", ids...)\n'  # a safe local
        '\tdb.Query("SELECT * FROM " + TABLE)\n'  # an UPPER_CASE constant
        '\tdb.ExecContext(ctx, "DELETE FROM t")\n'
        '\tlog.Printf("SELECT %s", query)\n'  # not a query method
        "}\n"
    )
    assert _sql(src) == []


def test_the_validator_refuses_foreign_shapes() -> None:
    good = _walk('package main\n\nfunc main() {\n\thttp.HandleFunc("/x", h)\n}\n')
    assert go_symbols_walk.valid(good)
    for bad in (
        ["main", "http.Nope", "n", "h", "", 4, 0, 0, "", ""],
        ["main", "http.HandleFunc", "n", "", "", 4, 0, 0, "", ""],
        ["main", "http.HandleFunc", "n", "h", "", 4, 4, 4, "", ""],
    ):
        assert not go_symbols_walk.valid({**good, "entries": [bad]}), bad
    assert not go_symbols_walk.valid({**good, "sql": [["main"]]})
    assert surface_records.valid(good, "go")
