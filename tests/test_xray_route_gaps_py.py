"""X7d route coverage, Python: the frameworks the first slice could not read.

Every rule is read from the one file unless the test goes through ``extract``.

- Router subclasses: a constructor whose name ends in ``Router`` takes ``prefix=``
  and one ending in ``Blueprint`` takes ``url_prefix=`` (``UserAPIRouter``).
- Django: a ``path`` route is rooted (``"login/"`` -> ``/login/``, ``""`` -> ``/``).
  A ``re_path`` regex becomes a route only when it is one: the anchors dropped,
  ``(?P<name>...)`` (no parenthesis inside) -> ``{name}``, ``\\.`` ``\\-`` ``\\/``
  unescaped; anything else left that a regex would read (``( ) [ ] ? * + | \\ ^ $``)
  makes the route "".
- A class-based view: ``X.as_view(...)`` as a handler names the class ``X``
  (form "n"), ``mod.X.as_view()`` names ``mod.X`` (form "a").
- DRF: ``R.register("prefix", ViewSet)`` on a name the file assigns from a
  constructor ending in ``Router`` is two entries, the list route and the detail
  route (``/prefix/`` and ``/prefix/{pk}/``; no trailing slash when the router is
  built with ``trailing_slash=False``). ``path("api/", include(R.urls))`` mounts
  ``R`` under that prefix. Any other ``register`` call is nothing, and neither
  ``register`` nor ``add_resource`` makes an entry as a decorator.
- flask-restx: ``@ns.route(p1, p2...)`` on a class and ``api.add_resource(Cls,
  p1, p2...)`` with ``Cls`` defined in the file are one "d" entry per view method
  the class defines (``surface_catalog.PY_VIEW_METHODS``) per path. A class the
  file does not define is one entry per path naming it, method "".
- Prefixes: ``Namespace("name", path=p)`` gives ``p`` (``/name`` with no
  ``path=``); a constructor ending in ``Api`` handed a name takes that name's
  prefix (``Api(bp)``); ``P.add_namespace(child, path=...)`` mounts like
  ``include_router``.
"""

from __future__ import annotations

import ast
import json

from gingugu.xray import symbols_walk
from gingugu.xray.lang import python

A = python.ADAPTER


def _raw(src: str) -> dict:
    raw = symbols_walk.walk(ast.parse(src))
    assert symbols_walk.valid(json.loads(json.dumps(raw)))
    return raw


def _routes(src: str) -> list[tuple[str, str, str]]:
    return [(r[1], r[8], r[9]) for r in _raw(src)["entries"]]


def _handlers(src: str) -> list[tuple[str, str, str, str, str]]:
    """(form, x, y, method, route) per entry."""
    return [(r[2], r[3], r[4], r[8], r[9]) for r in _raw(src)["entries"]]


def _linked(files: dict[str, str]) -> list[tuple[str, str]]:
    sym = A.extract({k: v.encode() for k, v in files.items()}, {}).symbols
    return sorted((e.method, e.route) for e in sym.entries if e.kind == "http")


# --- router subclasses ---------------------------------------------------------------


def test_a_router_subclass_keeps_its_prefix() -> None:
    src = (
        "r = UserAPIRouter(prefix='/users', tags=['x'])\n"
        "@r.get('/self')\ndef me(): pass\n"
        "bp = AdminBlueprint('admin', __name__, url_prefix='/admin')\n"
        "@bp.route('/stats')\ndef stats(): pass\n"
    )
    assert _routes(src) == [("r.get", "GET", "/users/self"), ("bp.route", "GET", "/admin/stats")]


def test_a_constructor_that_is_not_a_router_gives_no_prefix() -> None:
    src = "r = Thing(prefix='/users')\n@r.get('/self')\ndef me(): pass\n"
    assert _routes(src) == [("r.get", "GET", "/self")]


# --- Django --------------------------------------------------------------------------


def test_a_django_path_is_rooted() -> None:
    src = (
        "urlpatterns = [\n"
        "    path('login/', login),\n"
        "    path('', index),\n"
        "    path('bookmarks/<int:pk>/edit', edit),\n"
        "]\n"
    )
    assert [r for _, _, r in _routes(src)] == ["/login/", "/", "/bookmarks/<int:pk>/edit"]


def test_a_re_path_regex_becomes_a_route_only_when_it_is_one() -> None:
    cases = {
        r"^$": "/",
        r"^share/(?P<slug>[^/]+)$": "/share/{slug}",
        r"^logo/(?P<name>\w+)/$": "/logo/{name}/",
        r"^robots\.txt$": "/robots.txt",
        r"^a/(\d+)/$": "",
        r"^logo(?:/(?P<filename>.+))?/?$": "",
        r"^x/(?P<a>(b|c))/$": "",
        r"^files/.*$": "",
    }
    for regex, want in cases.items():
        got = _routes(f"urlpatterns = [re_path(r'{regex}', view)]\n")
        assert got == [("re_path", "", want)], regex


def test_a_class_based_view_is_named_by_its_class() -> None:
    src = (
        "urlpatterns = [\n"
        "    path('search/', GlobalSearchView.as_view()),\n"
        "    path('stats/', views.StatisticsView.as_view(), name='stats'),\n"
        "]\n"
        "app.add_url_rule('/users', view_func=UserAPI.as_view('users'))\n"
    )
    assert _handlers(src) == [
        ("n", "GlobalSearchView", "", "", "/search/"),
        ("a", "views", "StatisticsView", "", "/stats/"),
        ("n", "UserAPI", "", "GET", "/users"),
    ]


def test_a_call_that_is_not_as_view_is_still_no_handler() -> None:
    assert _routes("urlpatterns = [path('x/', make_view())]\n") == []


# --- DRF routers ---------------------------------------------------------------------


def test_a_drf_register_is_the_list_and_the_detail_route() -> None:
    src = (
        "api_router = DefaultRouter()\n"
        "api_router.register(r'documents', DocumentViewSet)\n"
        "api_router.register('', views.TagViewSet, basename='tag')\n"
    )
    assert _raw(src)["entries"] == [
        [
            "<module>",
            "api_router.register",
            "n",
            "DocumentViewSet",
            "",
            2,
            0,
            0,
            "",
            "/documents/",
            "api_router",
        ],  # noqa: E501
        [
            "<module>",
            "api_router.register",
            "n",
            "DocumentViewSet",
            "",
            2,
            0,
            0,
            "",
            "/documents/{pk}/",
            "api_router",
        ],  # noqa: E501
        [
            "<module>",
            "api_router.register",
            "a",
            "views",
            "TagViewSet",
            3,
            0,
            0,
            "",
            "/",
            "api_router",
        ],  # noqa: E501
        [
            "<module>",
            "api_router.register",
            "a",
            "views",
            "TagViewSet",
            3,
            0,
            0,
            "",
            "/{pk}/",
            "api_router",
        ],  # noqa: E501
    ]


def test_a_drf_router_without_trailing_slashes() -> None:
    src = "r = routers.SimpleRouter(trailing_slash=False)\nr.register('tags', TagViewSet)\n"
    assert [x[2] for x in _routes(src)] == ["/tags", "/tags/{pk}"]


def test_any_other_register_is_nothing() -> None:
    src = (
        "site.register(Bookmark, AdminBookmark)\n"
        "atexit.register(cleanup)\n"
        "atexit.register(cleanup, handler)\n"
        "reg = Registry()\n"
        "reg.register('name', handler)\n"
        "r = DefaultRouter()\n"
        "r.register(prefix, ViewSet)\n"
        "r.register('only-one-argument')\n"
        "register('tags', TagViewSet)\n"
    )
    assert _routes(src) == []


def test_a_call_only_registrar_makes_no_entry_as_a_decorator() -> None:
    src = (
        "@atexit.register\ndef bye(): pass\n"
        "@dispatch.register('x')\ndef on_x(): pass\n"
        "@admin.register(Model)\nclass A: pass\n"
        "@api.add_resource\ndef odd(): pass\n"
    )
    assert _routes(src) == []


def test_include_of_a_routers_urls_is_a_mount() -> None:
    src = "r = DefaultRouter()\nurlpatterns = [path('api/', include(r.urls))]\n"
    assert _raw(src)["mounts"] == [["<module>", 2, "", "api/", "n", "r", "", 0]]


def test_a_drf_router_under_a_django_include_end_to_end() -> None:
    files = {
        "site/__init__.py": "",
        "site/views.py": "class DocViewSet:\n    def list(self, request): pass\n",
        "site/urls.py": (
            "from site.views import DocViewSet\n"
            "api_router = DefaultRouter()\n"
            "api_router.register(r'documents', DocViewSet)\n"
            "urlpatterns = [\n"
            "    path('api/', include(api_router.urls)),\n"
            "    path('login/', login),\n"
            "]\n"
        ),
    }
    assert _linked(files) == [
        ("", "/api/documents/"),
        ("", "/api/documents/{pk}/"),
        ("", "/login/"),
    ]


# --- hostile input -------------------------------------------------------------------


def test_a_crafted_literal_never_reaches_a_route() -> None:
    src = (
        "r = DefaultRouter()\n"
        "r.register('ignore previous instructions', V)\n"
        "@ns.route('/a b')\nclass K:\n    def get(self): pass\n"
        "urlpatterns = [re_path(r'^say `hi`$', v)]\n"
    )
    assert all(r == "" for _, _, r in _routes(src))


_RES = "class Apps(Resource):\n    def get(self): pass\n    def post(self): pass\n"


def test_a_thousand_paths_on_one_resource_are_capped() -> None:
    paths = ", ".join(f"'/p{i}'" for i in range(1000))
    src = _RES + f"api.add_resource(Apps, {paths})\n"
    assert len(_raw(src)["entries"]) <= 2 * 16  # at most 16 paths per registration
