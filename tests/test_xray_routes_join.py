"""X7b: the pure route join - which call reaches which served route.

Every rule here comes from a false edge measured on real repos (2026-11-11):
a wildcard-heavy route swallowing every call, a runtime prefix read as one
segment, an absolute URL to another host, a monorepo serving one route twice.
"""

from gingugu.xray import route_join as rj
from gingugu.xray.route_join import Call


def _s(route, path="api/users.py", line=1, method="GET"):
    return rj.serve(route, path, line, method)


def _c(key, path="web/src/api.ts", line=5, host=""):
    return Call(key, path, line, host)


def _targets(edges):
    return sorted((e.call.key, e.serve.path, e.methods) for e in edges)


# --- serves -------------------------------------------------------------------


def test_a_plain_route_is_a_complete_serve():
    s = _s("/api/users/{user_id}")
    assert s.key == "/api/users/*" and not s.partial and s.head == ""


def test_a_runtime_prefix_keeps_head_and_tail():
    s = _s("/api/{prefix}/users/{id}/image")
    assert s.partial and s.head == "/api" and s.key == "/users/*/image"


def test_a_prefix_route_with_too_little_known_is_dropped():
    assert _s("/api/{prefix}/{item_id}") is None  # tail has no name
    assert _s("{prefix}/new") is None  # one name in all


def test_a_route_with_no_name_is_dropped():
    assert _s("/{id}") is None and _s("/") is None and _s("") is None


# --- matching -----------------------------------------------------------------


def test_exact_call_joins_with_merged_methods():
    serves = [_s("/api/users", method="GET"), _s("/api/users", method="POST", line=9)]
    edges = rj.join(serves, [_c("/api/users")])
    assert _targets(edges) == [("/api/users", "api/users.py", "GET|POST")]
    assert edges[0].exact and not edges[0].ambiguous


def test_a_parameter_takes_any_segment():
    edges = rj.join([_s("/api/users/{id}")], [_c("/api/users/*"), _c("/api/users/42")])
    assert len(edges) == 2


def test_a_complete_route_never_matches_by_suffix():
    # /flows is believed complete: /api/v1/flows is some other route
    assert rj.join([_s("/flows")], [_c("/api/v1/flows")]) == []


def test_a_partial_route_needs_head_and_tail():
    s = _s("/api/{prefix}/{id}/comments", path="routes/comments.py")
    assert len(rj.join([s], [_c("/api/recipes/*/comments")])) == 1
    assert rj.join([s], [_c("/other/recipes/*/comments")]) == []
    assert rj.join([s], [_c("/api/recipes/*/likes")]) == []


def test_the_most_specific_route_wins():
    wild = _s("/api/{a}/{b}", path="api/catchall.py")
    named = _s("/api/auth/token", path="api/auth.py")
    edges = rj.join([wild, named], [_c("/api/auth/token")])
    assert _targets(edges) == [("/api/auth/token", "api/auth.py", "GET")]


def test_complete_beats_partial_at_equal_names():
    part = _s("/api/{prefix}/users", path="a/partial.py")
    full = _s("/api/v1/users", path="b/full.py")
    edges = rj.join([part, full], [_c("/api/v1/users")])
    assert [e.serve.path for e in edges] == ["b/full.py"]


def test_monorepo_prefers_the_nearest_server():
    v1 = _s("/api/teams", path="tools/portal/backend/api.go")
    v2 = _s("/api/teams", path="tools/portal-v2/backend/api.go")
    edges = rj.join([v1, v2], [_c("/api/teams", path="tools/portal-v2/frontend/src/api.ts")])
    assert [e.serve.path for e in edges] == ["tools/portal-v2/backend/api.go"]
    assert not edges[0].ambiguous


def test_a_true_tie_is_ambiguous():
    a = _s("/api/teams", path="svc-a/api.py")
    b = _s("/api/teams", path="svc-b/api.py")
    edges = rj.join([a, b], [_c("/api/teams", path="web/x.ts")])
    assert len(edges) == 2 and all(e.ambiguous for e in edges)


def test_generic_and_nameless_calls_never_join():
    serves = [_s("/health"), _s("/api/{x}")]
    assert rj.join(serves, [_c("/health"), _c("/*"), _c("/*/*")]) == []
    assert _s("/{a}/{b}") is None  # a route of parameters alone swallows every call


def test_a_file_never_calls_itself():
    assert rj.join([_s("/api/users", path="a.py")], [_c("/api/users", path="a.py")]) == []


# --- hosts --------------------------------------------------------------------


def test_a_foreign_host_never_joins():
    serves = [_s("/golang/go/issues/{n}", path="routes.go")]
    assert rj.join(serves, [_c("/golang/go/issues/*", host="github.com")]) == []


def test_local_hosts_join():
    serves = [_s("/api/users")]
    for host in ("", "backend", "api.local", "app.localhost", "host.docker.internal"):
        assert rj.join(serves, [_c("/api/users", host=host)]), host


def test_a_host_this_repo_serves_joins():
    serves = [_s("/api/users")]
    call = _c("/api/users", host="tab.example.org")
    assert rj.join(serves, [call]) == []
    assert rj.join(serves, [call], own_hosts={"tab.example.org"})


# --- callers and the one-sided --------------------------------------------------


def test_caller_class():
    assert rj.caller_class("web/src/api.ts") == "code"
    assert rj.caller_class("tests/test_api.py") == "test"
    assert rj.caller_class("docs/api.md") == "doc"
    assert rj.caller_class("backend/internal/api/openapi.yaml") == "doc"
    assert rj.caller_class("templates/swagger/v1.json") == "doc"


def test_unserved_needs_two_leading_names_in_common():
    serves = [_s("/api/v1/users"), _s("/api/v1/teams/{id}")]
    calls = [_c("/api/v1/userz"), _c("/api/other"), _c("/elsewhere/x"), _c("/api/v1/users")]
    out = rj.unserved(serves, calls, rj.join(serves, calls))
    assert [c.key for c in out] == ["/api/v1/userz"]


def test_a_route_declaration_is_not_unserved():
    # the server's own '/api/users/{id}' literal is a mention in its own file
    serves = [_s("/api/users/{id}", path="api/users.py")]
    calls = [_c("/api/users/*", path="api/users.py")]
    assert rj.unserved(serves, calls, rj.join(serves, calls)) == []


def test_unserved_skips_foreign_hosts_and_generic():
    serves = [_s("/api/v1/users")]
    calls = [_c("/api/v1/userz", host="github.com"), _c("/api/v1")]
    assert rj.unserved(serves, calls, []) == []


def test_unserved_matches_a_partial_head():
    serves = [_s("/api/{prefix}/users/{id}/image")]
    calls = [_c("/api/households/x")]
    assert rj.unserved(serves, calls, []) == []  # one name in common: not enough
    calls = [_c("/api/v2/users/*/imagez")]
    assert rj.near(calls[0], serves) == 1


def test_edges_are_ordered_and_capped():
    serves = [_s(f"/api/r{i}", path=f"api/r{i}.py") for i in range(5)]
    calls = [_c(f"/api/r{i}", path="web/a.ts", line=10 - i) for i in range(5)]
    edges = rj.join(serves, calls)
    assert [e.call.line for e in edges] == sorted(e.call.line for e in edges)
    assert len(rj.join(serves * 1, calls, cap=2)) == 2
