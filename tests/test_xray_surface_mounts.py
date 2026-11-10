"""X7d spec: routes across files (``surface_mounts``) - the pure prefix chain."""

from __future__ import annotations

from gingugu.xray import surface_mounts as sm
from gingugu.xray import surface_route as sr

APP = ("main.py", "<module>", "app")
API = ("api/main.py", "<module>", "api_router")
USERS = ("api/routes/users.py", "<module>", "router")


def _m(parent, prefix, child, path="main.py", line=1):
    return sm.Mount(parent, prefix, child, path, line)


def test_an_unmounted_router_has_no_prefix() -> None:
    assert sm.full(USERS, []) == ("", 0)


def test_a_chain_of_mounts_is_joined_outermost_first() -> None:
    mounts = [_m(APP, "/api/v1", API), _m(API, "", USERS, "api/main.py")]
    assert sm.full(USERS, mounts) == ("/api/v1", 1)
    assert sm.full(API, mounts) == ("/api/v1", 1)


def test_a_runtime_prefix_is_a_placeholder_not_a_guess() -> None:
    mounts = [_m(APP, sr.PREFIX, API), _m(API, "/items", USERS, "api/main.py")]
    assert sm.full(USERS, mounts) == ("{prefix}/items", 1)


def test_a_cycle_stops() -> None:
    a, b = ("a.py", "<module>", "a"), ("b.py", "<module>", "b")
    mounts = [_m(a, "/x", b, "a.py"), _m(b, "/y", a, "b.py")]
    assert sm.full(b, mounts) == ("/y/x", 1)  # b <- a <- (b: seen) stops


def test_a_long_chain_stops_at_the_cap() -> None:
    refs = [(f"f{i}.py", "<module>", "r") for i in range(40)]
    mounts = [_m(refs[i + 1], "/p", refs[i], f"f{i + 1}.py") for i in range(39)]
    prefix, _ = sm.full(refs[0], mounts)
    assert prefix.count("/p") == sm.MAX_DEPTH


def test_several_mounts_take_the_first_and_count_them() -> None:
    mounts = [_m(APP, "/b", USERS, "z.py", 9), _m(APP, "/a", USERS, "a.py", 3)]
    assert sm.full(USERS, mounts) == ("/a", 2)


def test_every_mount_is_a_url() -> None:
    v = {n: ("main.go", "main", n) for n in ("v1", "v2", "v3")}
    art = ("main.go", "articleRouter", sm.ANY)
    mounts = [
        _m(v["v3"], "/v3/articles", art, line=32),
        _m(v["v2"], "/v2/articles", art, line=38),
        _m(v["v1"], "/v1/articles", art, line=45),
    ]
    ref = ("main.go", "articleRouter", "r")
    assert sm.prefixes(ref, mounts) == ["/v3/articles", "/v2/articles", "/v1/articles"]


def test_prefixes_branch_up_the_chain_and_stay_capped() -> None:
    mounts = [
        _m(APP, "/a", API, line=1),
        _m(APP, "/b", API, line=2),
        _m(API, "/u", USERS, "api/main.py", 1),
    ]
    assert sm.prefixes(USERS, mounts) == ["/a/u", "/b/u"]
    many = [_m(APP, f"/p{i}", USERS, line=i) for i in range(20)]
    assert len(sm.prefixes(USERS, many)) == sm.MAX_URLS


def test_prefixes_of_an_unmounted_router_is_one_empty_prefix() -> None:
    assert sm.prefixes(USERS, []) == [""]


def test_a_hostile_mount_count_stays_fast() -> None:
    import time

    mounts = [
        _m(("a.py", "<module>", f"p{i}"), "/x", ("b.py", "<module>", f"c{i}")) for i in range(3000)
    ]
    idx = sm.Index(mounts)
    start = time.monotonic()
    for i in range(20_000):
        sm.prefixes(("b.py", "<module>", f"c{i % 3000}"), idx)
    assert time.monotonic() - start < 2.0


def test_prefixes_survive_a_cycle() -> None:
    a, b = ("a.py", "<module>", "a"), ("b.py", "<module>", "b")
    mounts = [_m(a, "/x", b, "a.py"), _m(b, "/y", a, "b.py")]
    assert sm.prefixes(b, mounts) == ["/y/x"]


def test_any_matches_every_router_of_its_scope() -> None:
    built = ("admin.go", "adminRouter", sm.ANY)
    mounts = [_m(("main.go", "main", "r"), "/admin", built, "main.go")]
    assert sm.full(("admin.go", "adminRouter", "r"), mounts) == ("/admin", 1)
    assert sm.full(("admin.go", "other", "r"), mounts) == ("", 0)


def test_a_module_wide_any_matches_its_module_routes() -> None:
    urls = ("app/urls.py", "<module>", sm.ANY)
    mounts = [_m(("site/urls.py", "<module>", ""), "api/", urls, "site/urls.py")]
    assert sm.full(("app/urls.py", "<module>", ""), mounts) == ("api/", 1)


def test_a_mount_on_an_unnamed_parent_still_prefixes() -> None:
    assert sm.full(USERS, [_m(None, "/v2", USERS)]) == ("/v2", 1)


def test_route_keeps_the_leading_slash() -> None:
    assert sm.route("/api/v1", "/users/me") == "/api/v1/users/me"
    assert sm.route("api", "/cats") == "/api/cats"  # Nest: setGlobalPrefix('api')
    assert sm.route("api/", "items/<int:pk>/") == "api/items/<int:pk>/"  # Django: no slash
    assert sm.route(sr.PREFIX, "/me") == "{prefix}/me"
    assert sm.route("/api", "") == ""  # a route that is not literal stays unknown
    assert sm.route("", "/x") == "/x"


def test_project_dir() -> None:
    assert sm.project_dir("01-cats-app/src/main.ts") == "01-cats-app"
    assert sm.project_dir("src/main.ts") == ""
    assert sm.project_dir("apps/api/main.ts") == "apps/api"
    assert sm.under("01-cats-app/src/cats/c.ts", "01-cats-app")
    assert not sm.under("02-gateways/src/c.ts", "01-cats-app")
    assert sm.under("anything.ts", "")
