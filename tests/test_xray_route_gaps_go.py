"""X7d route coverage, Go: group closures, empty paths, wrapped handlers, and
verbs that are not routes.

All read from one file:

- A group closure: ``R.Group(path, func() {...}, mw...)`` gives ``R`` the prefix
  ``join(prefix(R), path)`` inside the function literal only, so closures nest
  (gitea's ``m.Group("/user", func() { m.Get("/login", h) })``). When the literal
  takes a parameter, that parameter gets the prefix instead and ``R`` keeps its
  own. A path that is not a literal makes the prefix ``surface_route.PREFIX``.
- An empty path literal (``g.GET("", h)``) is the receiver's prefix itself; with
  no prefix the route stays "".
- A router verb (every ``_GO_LAST`` name but ``Handle``/``HandleFunc``/``Method``/
  ``MethodFunc``) whose first argument is a string literal that is neither empty
  nor starts with ``/`` registers nothing (``slog.Any("error", err)``,
  ``cache.Get("key", fallback)``), and so does one whose first argument is a call
  or a function literal (``httphandler.GET(httphandler.Func(h), fallback)`` wraps a
  handler, it does not register one). A first argument that is a name or ``x.y``
  (a path held in a variable or constant) keeps today's rule: an entry, route "".
- A wrapped handler: when the last argument is a call, the handler is found in
  that call's first argument, up to 4 calls deep - a name ("n"), ``x.y`` on a
  bare name ("a"), or a function literal ("i") (``mux.Handle("/f",
  httphandler.GET(httphandler.Func(s.serve), fallback))``).
"""

from __future__ import annotations

import json

from gingugu.xray.lang import golang

A = golang.ADAPTER


def _entries(body: str) -> list[list]:
    src = f"package x\n\nfunc setup(m *Router, r *Router) {{\n{body}}}\n"
    raw = A.parse("a.go", src.encode())
    assert A.valid(json.loads(json.dumps(raw)))
    return raw["sym"]["entries"]


def _routes(body: str) -> list[tuple[str, str, str]]:
    return [(r[1], r[8], r[9]) for r in _entries(body)]


# --- group closures ------------------------------------------------------------------


def test_a_group_closure_prefixes_its_receiver_inside_only() -> None:
    body = (
        'm.Group("/user", func() {\n'
        '\tm.Get("/login", h)\n'
        '\tm.Group("/settings", func() {\n'
        '\t\tm.Post("/keys", h)\n'
        "\t})\n"
        '\tm.Group("", func() {\n'
        '\t\tm.Get("/x", h)\n'
        "\t})\n"
        '\tm.Delete("/logout", h)\n'
        "})\n"
        'm.Get("/after", h)\n'
    )
    assert _routes(body) == [
        ("m.Get", "GET", "/user/login"),
        ("m.Post", "POST", "/user/settings/keys"),
        ("m.Get", "GET", "/user/x"),
        ("m.Delete", "DELETE", "/user/logout"),
        ("m.Get", "GET", "/after"),
    ]


def test_a_group_closure_with_middleware_after_it() -> None:
    body = 'm.Group("/admin", func() {\n\tm.Get("/users", h)\n}, reqSignIn, adminReq)\n'
    assert _routes(body) == [("m.Get", "GET", "/admin/users")]


def test_a_group_closure_under_a_runtime_path() -> None:
    body = 'm.Group(base, func() {\n\tm.Get("/users", h)\n})\n'
    assert _routes(body) == [("m.Get", "GET", "{prefix}/users")]


def test_a_group_closure_that_takes_a_parameter() -> None:
    body = (
        'r.Group("/v1", func(g *gin.RouterGroup) {\n'
        '\tg.GET("/x", h)\n'
        '\tr.GET("/y", h)\n'
        "})\n"
    )
    assert _routes(body) == [("g.GET", "GET", "/v1/x"), ("r.GET", "GET", "/y")]


def test_a_group_closure_inside_an_assigned_group() -> None:
    body = 'api := r.Group("/api")\napi.Group("/v1", func() {\n\tapi.GET("/x", h)\n})\n'
    assert _routes(body) == [("api.GET", "GET", "/api/v1/x")]


def test_the_chi_forms_are_unchanged() -> None:
    body = (
        'r.Route("/a", func(p chi.Router) {\n\tp.Get("/b", h)\n})\n'
        'r.Group(func(p chi.Router) {\n\tp.Get("/c", h)\n})\n'
    )
    assert _routes(body) == [("p.Get", "GET", "/a/b"), ("p.Get", "GET", "/c")]


def test_a_group_closure_keeps_its_root_router_as_receiver() -> None:
    body = 'm.Group("/user", func() {\n\tm.Get("/login", h)\n})\n'
    assert _entries(body)[0][10] == "#0"  # m is setup's first parameter, inside too


# --- empty paths ---------------------------------------------------------------------


def test_an_empty_path_is_the_prefix_itself() -> None:
    body = (
        'g := r.Group("/v1")\n'
        'g.GET("", h)\n'
        'm.Group("/secrets", func() {\n\tm.Get("", h)\n\tm.Post("", h)\n})\n'
        'r.GET("", h)\n'
    )
    assert _routes(body) == [
        ("g.GET", "GET", "/v1"),
        ("m.Get", "GET", "/secrets"),
        ("m.Post", "POST", "/secrets"),
        ("r.GET", "GET", ""),
    ]


def test_a_path_that_is_not_a_literal_is_not_the_prefix() -> None:
    body = 'g := r.Group("/v1")\ng.GET(path, h)\n'
    assert _routes(body) == [("g.GET", "GET", "")]


# --- verbs that are not routes -------------------------------------------------------


def test_a_verb_with_a_literal_that_is_no_route_registers_nothing() -> None:
    body = (
        'slog.Any("error", err)\n'
        'v := cache.Get("key", fallback)\n'
        'q.Delete("users", cond)\n'
        'zap.Any("ctx", func() {})\n'
        'r.Get("/x", h)\n'
        'r.Any("/y", h)\n'
    )
    assert _routes(body) == [("r.Get", "GET", "/x"), ("r.Any", "", "/y")]


def test_a_verb_handed_a_call_or_a_function_first_registers_nothing() -> None:
    body = (
        "x := httphandler.GET(httphandler.Func(serve), fallback)\n"
        "y := wrap.Post(func() {}, h)\n"
        "r.GET(routes.Users, h)\n"
        "r.POST(path, h)\n"
    )
    assert _routes(body) == [("r.GET", "GET", ""), ("r.POST", "POST", "")]


def test_handle_patterns_are_not_held_to_the_slash_rule() -> None:
    body = (
        'mux.HandleFunc("GET /items/{id}", h)\n'
        'mux.Handle("example.com/x", h)\n'
        'r.Method("GET", "/z", h)\n'
    )
    assert _routes(body) == [
        ("mux.HandleFunc", "GET", "/items/{id}"),
        ("mux.Handle", "", "example.com/x"),
        ("r.Method", "GET", "/z"),
    ]


# --- wrapped handlers ----------------------------------------------------------------


def test_a_wrapped_handler_is_found_through_its_wrappers() -> None:
    body = (
        'mux.Handle("/file/{uid}", httphandler.GET(httphandler.Func(s.serve), fallback))\n'
        'mux.Handle("/health", http.HandlerFunc(health))\n'
        'mux.Handle("/sse", wrap(func(w http.ResponseWriter, q *http.Request) {\n\tgo run()\n}))\n'
    )
    got = [(r[2], r[3], r[4], r[9]) for r in _entries(body)]
    assert got == [
        ("a", "s", "serve", "/file/{uid}"),
        ("n", "health", "", "/health"),
        ("i", "", "", "/sse"),
    ]
    inline = _entries(body)[2]
    assert (inline[6], inline[7]) == (6, 8)  # the literal's own line span


def test_a_wrapper_with_nothing_handler_shaped_registers_nothing() -> None:
    body = 'mux.Handle("/a", build())\nmux.Handle("/b", wrap(1, h))\n'
    assert _routes(body) == []


def test_wrappers_are_followed_four_calls_deep_and_no_further() -> None:
    assert len(_routes('mux.Handle("/a", w1(w2(w3(w4(h)))))\n')) == 1
    assert _routes('mux.Handle("/a", w1(w2(w3(w4(w5(h))))))\n') == []


def test_a_last_argument_that_is_a_name_is_unchanged() -> None:
    body = 'r.Get("/x", web.Bind(form), handler)\n'
    assert [(r[2], r[3]) for r in _entries(body)] == [("n", "handler")]


# --- hostile input -------------------------------------------------------------------


def test_ten_thousand_nested_groups_do_not_cost_the_file() -> None:
    depth = 10_000
    body = 'm.Group("/a", func() {\n' * depth + 'm.Get("/x", h)\n' + "})\n" * depth
    routes = _routes(body)  # must return, not raise; the route is clean or ""
    assert len(routes) <= 1


def test_thousands_of_groups_and_closures_in_one_function_are_bounded() -> None:
    import time

    n = 6_000
    body = "".join(f'g{i} := r.Group("/x")\n' for i in range(n))
    body += 'r.Route("/y", func(p chi.Router) {})\n' * n
    start = time.monotonic()
    _routes(body)
    assert time.monotonic() - start < 20  # was quadratic: every closure copied every name
