"""X7d spec, Go: routes across files - the URL, not just the file's tail.

End to end through ``golang.ADAPTER.extract``. The walk adds ``recv`` to every
entry and ``mounts`` records (``surface_records``). ``recv`` is the root of the
receiver after following same-function group aliases (``v1 := r.Group(...)``:
``v1.GET`` has ``recv`` ``r``); a root that is the enclosing function's i-th
parameter is ``#<i>`` (receiver parameters are not counted).

Mount sites:

- ``P.Mount("/p", child)`` (chi): ``child`` a call ``f()`` / ``pkg.F()`` -> form
  ``cn``/``ca`` (every router that function builds), or a name -> ``n``.
- A router passed into a repo function, ``F(v1)`` / ``pkg.F(v1)``, where the
  argument is a group or router the walk tracks in this function (bound from
  ``Group``/``Route``, or from ``chi.NewRouter()``, ``gin.New()``,
  ``gin.Default()``, ``echo.New()``, ``http.NewServeMux()``) or a parameter that
  is itself a receiver root: form ``pn``/``pa``, ``arg`` its index, ``parent``
  the argument's root, ``prefix`` the argument's same-function group prefix.

Value resolution (``value(rel, scope, form, x, y)``), Go: ``"n"`` ``x`` bound in
``scope`` -> ``(rel, scope, x)``; else a package-level ``var`` of the same
package (any file in that directory) -> ``(that file, "<module>", x)``; else None.
Calls resolve as the call graph resolves them (``pkg.F`` through the import).
"""

from __future__ import annotations

import json

from gingugu.xray.lang import golang

A = golang.ADAPTER
GOMOD = {"go.mod": b"module example.com/app\n\ngo 1.22\n"}


def _routes(files: dict[str, str]) -> dict[str, str]:
    sym = A.extract({k: v.encode() for k, v in files.items()}, GOMOD).symbols
    return {e.handler: e.route for e in sym.entries if e.kind == "http"}


def test_the_walk_records_round_trip() -> None:
    src = (
        "package main\n\n"
        "func main() {\n"
        "\tr := chi.NewRouter()\n"
        '\tr.Mount("/admin", adminRouter())\n'
        '\tr.Get("/", home)\n'
        "}\n"
    )
    raw = A.parse("main.go", src.encode())
    assert raw["sym"]["mounts"] == [["main", 5, "r", "/admin", "cn", "adminRouter", "", 0]]
    assert raw["sym"]["entries"][-1][10] == "r"
    assert A.valid(json.loads(json.dumps(raw)))


def test_chi_mount_of_a_builder_function() -> None:
    files = {
        "main.go": (
            "package main\n\n"
            "func main() {\n"
            "\tr := chi.NewRouter()\n"
            '\tr.Get("/", home)\n'
            '\tr.Mount("/admin", adminRouter())\n'
            "}\n"
            "func home() {}\n"
        ),
        "admin.go": (
            "package main\n\n"
            "func adminRouter() http.Handler {\n"
            "\tr := chi.NewRouter()\n"
            '\tr.Get("/accounts", accounts)\n'
            "\treturn r\n"
            "}\n"
            "func accounts() {}\n"
        ),
    }
    assert _routes(files) == {"home": "/", "accounts": "/admin/accounts"}


def test_a_gin_group_passed_into_another_package() -> None:
    files = {
        "main.go": (
            "package main\n\n"
            'import "example.com/app/routes"\n\n'
            "func main() {\n"
            "\trouter := gin.Default()\n"
            '\tv1 := router.Group("/v1")\n'
            "\troutes.AddUserRoutes(v1)\n"
            "}\n"
        ),
        "routes/users.go": (
            "package routes\n\n"
            "func AddUserRoutes(rg *gin.RouterGroup) {\n"
            '\tusers := rg.Group("/users")\n'
            '\tusers.GET("/", list)\n'
            "}\n"
            "func list() {}\n"
        ),
    }
    assert _routes(files) == {"list": "/v1/users/"}


def test_a_package_level_router_mounted_by_name() -> None:
    files = {
        "main.go": (
            "package main\n\n"
            "func main() {\n"
            "\tr := chi.NewRouter()\n"
            '\tr.Mount("/api", apiRouter)\n'
            "}\n"
        ),
        "api.go": (
            "package main\n\n"
            "var apiRouter = chi.NewRouter()\n\n"
            "func init() {\n"
            '\tapiRouter.Get("/status", status)\n'
            "}\n"
            "func status() {}\n"
        ),
    }
    assert _routes(files) == {"status": "/api/status"}


def test_a_router_mounted_three_times_answers_at_three_urls() -> None:
    files = {
        "main.go": (
            "package main\n\n"
            "func main() {\n"
            "\tr := chi.NewRouter()\n"
            '\tr.Route("/v2", func(r chi.Router) {\n'
            '\t\tr.Mount("/articles", articleRouter())\n'
            "\t})\n"
            '\tr.Route("/v1", func(r chi.Router) {\n'
            '\t\tr.Mount("/articles", articleRouter())\n'
            "\t})\n"
            "}\n"
            "func articleRouter() http.Handler {\n"
            "\tr := chi.NewRouter()\n"
            '\tr.Get("/", listArticles)\n'
            "\treturn r\n"
            "}\n"
            "func listArticles() {}\n"
        )
    }
    sym = A.extract({k: v.encode() for k, v in files.items()}, GOMOD).symbols
    assert [e.route for e in sym.entries if e.kind == "http"] == ["/v2/articles/", "/v1/articles/"]


def test_an_unresolvable_mount_changes_nothing() -> None:
    files = {
        "main.go": (
            "package main\n\n"
            "func main() {\n"
            "\tr := chi.NewRouter()\n"
            '\tr.Mount("/x", other.Router())\n'
            '\tr.Get("/own", own)\n'
            "}\n"
            "func own() {}\n"
        )
    }
    assert _routes(files) == {"own": "/own"}
