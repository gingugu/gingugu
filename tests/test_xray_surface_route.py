"""X7d spec: an entry's method and route (``surface_route``)."""

from __future__ import annotations

import pytest

from gingugu.xray import surface_route as sr


@pytest.mark.parametrize(
    "raw",
    ["/users/{user_id}", ":id", "file://documents/{name}", "get_weather", "/a?b=c&d", "/v1/*"],
)
def test_a_route_literal_is_kept_whole(raw: str) -> None:
    assert sr.clean(raw) == raw and sr.is_route(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "a b",
        "/x\n",
        "/x\t",
        '/"x',
        "/`x`",
        "/x\\y",
        "/ü",
        "\x1b[31m",
        "x" * 257,
        None,
        3,
        ["/x"],
    ],
)
def test_anything_unsafe_to_render_is_dropped(raw: object) -> None:
    assert sr.clean(raw) == ""
    if raw != "":
        assert not sr.is_route(raw)


def test_the_empty_route_is_a_valid_cache_value() -> None:
    assert sr.is_route("")


@pytest.mark.parametrize(
    "label, method",
    [
        ("app.get", "GET"),
        ("router.POST", "POST"),
        ("r.Delete", "DELETE"),
        ("Patch", "PATCH"),
        ("app.websocket", "WS"),
        ("app.route", ""),
        ("app.all", ""),
        ("r.Any", ""),
        ("http.HandleFunc", ""),
        ("mcp.tool", ""),
        ("app.add_url_rule", ""),
    ],
)
def test_a_verb_names_its_method(label: str, method: str) -> None:
    assert sr.method_of(label) == method


def test_a_methods_list_is_joined_in_a_fixed_order() -> None:
    assert sr.methods(["post", "GET"]) == "GET|POST"
    assert sr.methods(["GET", "GET"]) == "GET"
    assert sr.methods([]) == ""
    assert sr.methods(["GET", None]) == ""  # a non-literal member: the set is unknown
    assert sr.methods(["GET", "BREW"]) == ""


@pytest.mark.parametrize("v", ["", "GET", "GET|POST", "POST|DELETE|WS"])
def test_valid_methods(v: str) -> None:
    assert sr.is_method(v)


@pytest.mark.parametrize("v", ["get", "POST|GET", "GET|GET", "GET|", "BREW", None, 1, "GET POST"])
def test_invalid_methods(v: object) -> None:
    assert not sr.is_method(v)


def test_a_go_pattern_splits_its_method_off() -> None:
    assert sr.go_pattern("GET /items/{id}") == ("GET", "/items/{id}")
    assert sr.go_pattern("POST example.com/x") == ("POST", "example.com/x")
    assert sr.go_pattern("/plain") == ("", "/plain")
    assert sr.go_pattern("BREW /x") == ("", "BREW /x")  # not a method: left whole


@pytest.mark.parametrize(
    "prefix, route, joined",
    [
        ("/users", "/me", "/users/me"),
        ("/users", "/", "/users/"),
        ("/users/", "/me", "/users/me"),
        ("cats", ":id", "cats/:id"),
        ("", "/me", "/me"),
        ("/v1", "", "/v1"),
        ("", "", ""),
        (sr.PREFIX, "/me", "{prefix}/me"),
    ],
)
def test_join(prefix: str, route: str, joined: str) -> None:
    assert sr.join(prefix, route) == joined


def test_a_join_too_long_to_render_is_empty() -> None:
    assert sr.join("/" + "a" * 200, "/" + "b" * 100) == ""


def test_the_label_an_agent_reads() -> None:
    assert sr.label("http", "POST", "/users", "router.post") == "POST /users"
    assert sr.label("http", "", "/users", "path") == "/users"
    assert sr.label("http", "GET", "", "router.get") == "GET (route not literal)"
    assert sr.label("http", "", "", "mx.Handle") == "mx.Handle (route not literal)"
    assert sr.label("mcp", "", "get_weather", "mcp.tool") == "get_weather"
    assert sr.label("main", "", "", "__main__") == "__main__"
