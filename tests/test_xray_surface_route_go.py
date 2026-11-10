"""X7d spec, Go walk: what each entry answers to (``surface_route``).

Every ``entries`` record ends ``..., method, route``. The rules, from one file:

- ``recv.VERB(path, ...)`` (gin ``GET``, chi/echo ``Get``...): the verb is the
  method (``Any`` is ""); ``HandleFunc``/``Handle``: a Go 1.22 pattern's method
  (``"GET /x"``, ``surface_route.go_pattern``), else "".
- The path is the first argument when it is a string literal (interpreted or
  raw); anything else is "".
- A group prefix, tracked per function: ``g := r.Group("/v1")`` (gin, echo; extra
  middleware arguments allowed; ``=`` as well as ``:=``) gives ``g`` the prefix
  ``join(prefix(r), "/v1")``, so groups chain. chi ``r.Route("/x", func(r chi.Router)
  {...})`` gives the literal's first parameter that prefix inside its body, and
  ``r.Group(func(r chi.Router) {...})`` gives it the outer prefix unchanged. A
  ``Group``/``Route`` path that is not a literal makes the prefix
  ``surface_route.PREFIX``.
- ``func main``: method and route "".
Every route passes ``surface_route.clean``.
"""

from __future__ import annotations

import json

from gingugu.xray import surface_route as sr
from gingugu.xray.lang import golang

A = golang.ADAPTER


def _routes(body: str, pkg: str = "x") -> list[tuple[str, str, str]]:
    src = f"package {pkg}\n\nfunc setup(r *Router) {{\n{body}}}\n"
    raw = A.parse("a.go", src.encode())
    assert A.valid(json.loads(json.dumps(raw)))
    return [(r[1], r[8], r[9]) for r in raw["sym"]["entries"]]


def test_every_record_has_ten_fields() -> None:
    raw = A.parse("a.go", b'package x\n\nfunc s() {\n\thttp.HandleFunc("/x", h)\n}\n')["sym"]
    assert raw["entries"] == [["s", "http.HandleFunc", "n", "h", "", 4, 0, 0, "", "/x"]]


def test_main_answers_to_nothing() -> None:
    raw = A.parse("a.go", b"package main\n\nfunc main() {}\n")["sym"]
    assert [(r[8], r[9]) for r in raw["entries"]] == [("", "")]


def test_verbs_and_patterns() -> None:
    body = (
        '\tr.GET("/a", a)\n'
        '\tr.Post("/b", b)\n'
        '\tr.Any("/c", c)\n'
        '\thttp.HandleFunc("GET /items/{id}", item)\n'
        '\tmux.Handle("/static/", files)\n'
        "\tr.DELETE(`/raw`, d)\n"
        "\tmx.Handle(pattern, fn)\n"
    )
    assert _routes(body) == [
        ("r.GET", "GET", "/a"),
        ("r.Post", "POST", "/b"),
        ("r.Any", "", "/c"),
        ("http.HandleFunc", "GET", "/items/{id}"),
        ("mux.Handle", "", "/static/"),
        ("r.DELETE", "DELETE", "/raw"),
        ("mx.Handle", "", ""),
    ]


def test_gin_groups_chain() -> None:
    body = (
        '\tv1 := r.Group("/v1")\n'
        "\t{\n"
        '\t\tv1.POST("/login", login)\n'
        '\t\tadmin := v1.Group("/admin", auth)\n'
        '\t\tadmin.GET("/stats", stats)\n'
        "\t}\n"
        '\tr.GET("/health", health)\n'
        "\tvar g = r.Group(base)\n"
        '\tg.GET("/x", x)\n'
    )
    assert [r[2] for r in _routes(body)] == [
        "/v1/login",
        "/v1/admin/stats",
        "/health",
        sr.PREFIX + "/x",
    ]


def test_a_group_does_not_leak_out_of_its_function() -> None:
    src = (
        "package x\n\n"
        'func a(r *R) {\n\tg := r.Group("/a")\n\tg.GET("/x", x)\n}\n'
        'func b(g *R) {\n\tg.GET("/y", y)\n}\n'
    )
    raw = A.parse("a.go", src.encode())["sym"]
    assert [r[9] for r in raw["entries"]] == ["/a/x", "/y"]


def test_chi_route_and_group_scope_the_closure_parameter() -> None:
    body = (
        '\tr.Route("/articles", func(r chi.Router) {\n'
        '\t\tr.Get("/", list)\n'
        '\t\tr.Route("/{id}", func(sub chi.Router) {\n'
        '\t\t\tsub.Put("/", update)\n'
        "\t\t})\n"
        "\t\tr.Group(func(r chi.Router) {\n"
        '\t\t\tr.Delete("/{id}", remove)\n'
        "\t\t})\n"
        "\t})\n"
        '\tr.Get("/ping", ping)\n'
    )
    assert _routes(body) == [
        ("r.Get", "GET", "/articles/"),
        ("sub.Put", "PUT", "/articles/{id}/"),
        ("r.Delete", "DELETE", "/articles/{id}"),
        ("r.Get", "GET", "/ping"),
    ]


def test_an_unsafe_literal_is_dropped() -> None:
    body = '\tr.GET("/x y", a)\n\tr.GET("/q\\"x", b)\n'
    assert [r[2] for r in _routes(body)] == ["", ""]
