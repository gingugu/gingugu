"""X7d route coverage, Python: flask-restx and flask-restful class resources.

The rules are the flask-restx and prefix paragraphs of
``test_xray_route_gaps_py``'s docstring; split out for the file-size limit.
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


# --- flask-restx ---------------------------------------------------------------------

RESOURCE = (
    "class Apps(Resource):\n"
    "    def get(self): pass\n"
    "    def post(self): pass\n"
    "    def helper(self): pass\n"
)


def test_a_route_decorated_class_is_an_entry_per_view_method_per_path() -> None:
    src = "@ns.route('/apps', '/apps/all')\n" + RESOURCE
    got = _raw(src)["entries"]
    assert sorted(got) == sorted(
        [
            ["<module>", "ns.route", "d", "Apps.get", "", 1, 0, 0, "GET", "/apps", "ns"],
            ["<module>", "ns.route", "d", "Apps.get", "", 1, 0, 0, "GET", "/apps/all", "ns"],
            ["<module>", "ns.route", "d", "Apps.post", "", 1, 0, 0, "POST", "/apps", "ns"],
            ["<module>", "ns.route", "d", "Apps.post", "", 1, 0, 0, "POST", "/apps/all", "ns"],
        ]
    )


def test_a_route_decorated_class_with_no_view_method_is_nothing() -> None:
    assert _routes("@ns.route('/x')\nclass Odd:\n    def run(self): pass\n") == []


def test_a_route_decorated_function_is_unchanged() -> None:
    assert _routes("@bp.route('/x', methods=['POST'])\ndef x(): pass\n") == [
        ("bp.route", "POST", "/x")
    ]


def test_add_resource_of_a_class_in_the_file() -> None:
    src = RESOURCE + "api.add_resource(Apps, '/apps', '/apps/<int:app_id>')\n"
    got = sorted((r[1], r[2], r[3], r[8], r[9]) for r in _raw(src)["entries"])
    assert got == sorted(
        [
            ("api.add_resource", "d", "Apps.get", "GET", "/apps"),
            ("api.add_resource", "d", "Apps.get", "GET", "/apps/<int:app_id>"),
            ("api.add_resource", "d", "Apps.post", "POST", "/apps"),
            ("api.add_resource", "d", "Apps.post", "POST", "/apps/<int:app_id>"),
        ]
    )
    assert {r[5] for r in _raw(src)["entries"]} == {5}  # the registering call's line


def test_add_resource_of_a_class_defined_later_in_the_file() -> None:
    src = "api.add_resource(Apps, '/apps')\n" + RESOURCE
    assert sorted(_routes(src)) == [
        ("api.add_resource", "GET", "/apps"),
        ("api.add_resource", "POST", "/apps"),
    ]


def test_add_resource_of_a_class_from_elsewhere_names_the_class() -> None:
    src = "api.add_resource(Banner, '/banners')\napi.add_resource(mod.Events, '/a', '/b')\n"
    assert _handlers(src) == [
        ("n", "Banner", "", "", "/banners"),
        ("a", "mod", "Events", "", "/a"),
        ("a", "mod", "Events", "", "/b"),
    ]


def test_add_resource_with_no_literal_path_is_nothing() -> None:
    assert _routes("api.add_resource(Banner, url)\napi.add_resource(Banner)\n") == []


def test_namespace_and_api_prefixes() -> None:
    src = (
        "a = Namespace('console', description='x', path='/c')\n"
        "b = Namespace('files')\n"
        "c = Namespace('root', path='/')\n"
        "bp = Blueprint('console', __name__, url_prefix='/console/api')\n"
        "api = ExternalApi(bp, version='1.0')\n"
        "@a.route('/x')\n"
        + RESOURCE.replace("Apps", "A1")
        + "@b.route('/x')\n"
        + RESOURCE.replace("Apps", "B1")
        + "@c.route('/x')\n"
        + RESOURCE.replace("Apps", "C1")
        + "api.add_resource(Banner, '/banners')\n"
    )
    assert sorted({r for _, _, r in _routes(src)}) == [
        "/c/x",
        "/console/api/banners",
        "/files/x",
        "/x",
    ]


def test_add_namespace_is_a_mount() -> None:
    src = (
        "bp = Blueprint('console', __name__, url_prefix='/console/api')\n"
        "api = Api(bp)\n"
        "api.add_namespace(ns)\n"
        "api.add_namespace(other, path='/v2')\n"
    )
    assert _raw(src)["mounts"] == [
        ["<module>", 3, "api", "/console/api", "n", "ns", "", 0],
        ["<module>", 4, "api", "/console/api/v2", "n", "other", "", 0],
    ]


def test_a_restx_namespace_mounted_in_another_file_end_to_end() -> None:
    files = {
        "c/__init__.py": (
            "bp = Blueprint('console', __name__, url_prefix='/console/api')\n"
            "api = ExternalApi(bp)\n"
            "console_ns = Namespace('console', path='/')\n"
            "api.add_namespace(console_ns)\n"
        ),
        "c/apps.py": (
            "from c import console_ns\n"
            "@console_ns.route('/apps/<uuid:app_id>')\n"
            "class AppApi(Resource):\n"
            "    def get(self, app_id): pass\n"
            "    def delete(self, app_id): pass\n"
        ),
    }
    assert _linked(files) == [
        ("DELETE", "/console/api/apps/<uuid:app_id>"),
        ("GET", "/console/api/apps/<uuid:app_id>"),
    ]
