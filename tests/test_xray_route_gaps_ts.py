"""X7d route coverage, TS/JS: Next.js app-router file routes and unrooted routes.

- Unrooted routes: an HTTP route literal that is not empty and starts with
  neither ``/`` nor ``*`` is rooted (koa-router ``router.post("documents.info")``
  answers at ``/documents.info``). MCP names and NestJS routes are untouched.
- Next.js app router: a file ``route.<ext>`` under a directory named ``app``
  answers the URL its path spells, once per method it exports. The parse cache is
  keyed by content, so the walk cannot know the path: it records every exported
  top-level function (declaration, or ``const`` bound to a function) named for a
  method (``surface_catalog.NEXT_VERBS``) as a "d" entry with label and method the
  verb and route "", in any file. The linker then gives it the route
  ``ts_surface_next.route_of(path)`` and drops it when that is None.
- ``route_of(rel)``: None unless the last segment is ``route`` + one of ``.ts
  .tsx .js .jsx .mjs .mts`` and some earlier segment is exactly ``app``. The
  segments after the FIRST ``app``: ``(group)`` and ``@slot`` segments dropped,
  ``[id]`` -> ``{id}``, ``[...rest]`` and ``[[...rest]]`` -> ``{rest}``; joined
  under ``/`` (``app/route.ts`` is ``/``). A segment ``surface_route.clean``
  refuses makes it None.
"""

from __future__ import annotations

import json

import pytest

from gingugu.xray.lang import ts_surface_next, typescript

A = typescript.ADAPTER


def _entries(src: str, rel: str = "a.ts") -> list[list]:
    raw = A.parse(rel, src.encode())
    assert A.valid(json.loads(json.dumps(raw)))
    return raw["sym"]["entries"]


def _routes(src: str) -> list[tuple[str, str, str]]:
    return [(r[1], r[8], r[9]) for r in _entries(src)]


def _linked(files: dict[str, str]) -> list[tuple[str, str, str]]:
    sym = A.extract({k: v.encode() for k, v in files.items()}, {}).symbols
    return sorted((e.path, e.method, e.route) for e in sym.entries if e.kind == "http")


# --- unrooted routes -----------------------------------------------------------------


def test_an_unrooted_route_is_rooted() -> None:
    src = (
        'router.post("documents.delete", h);\n'
        'app.get("users/:id", h);\n'
        "app.get(`teams`, h);\n"
        'app.route("books").get(h).post(h);\n'
    )
    assert _routes(src) == [
        ("router.post", "POST", "/documents.delete"),
        ("app.get", "GET", "/users/:id"),
        ("app.get", "GET", "/teams"),
        ("app.get", "GET", "/books"),
        ("app.post", "POST", "/books"),
    ]


def test_rooted_wildcard_and_empty_routes_are_unchanged() -> None:
    src = 'app.get("/x", h);\napp.all("*", h);\napp.get("", h);\n'
    assert _routes(src) == [("app.get", "GET", "/x"), ("app.all", "", "*"), ("app.get", "GET", "")]


def test_names_that_are_not_http_routes_are_never_rooted() -> None:
    src = (
        'server.tool("get_weather", h);\n'
        "@Controller('cats')\nclass C {\n  @Get('all')\n  all() {}\n}\n"
    )
    assert [r for _, _, r in _routes(src)] == ["get_weather", "/cats/all"]


def test_an_unrooted_route_under_a_mount_end_to_end() -> None:
    files = {
        "server/docs.ts": (
            'import Router from "koa-router";\n'
            "const router = new Router();\n"
            'router.post("documents.info", async (ctx) => {});\n'
            "export default router;\n"
        ),
        "server/index.ts": (
            'import Router from "koa-router";\n'
            'import docs from "./docs";\n'
            "const api = new Router();\n"
            'api.use("/api", docs);\n'
        ),
    }
    assert _linked(files) == [("server/docs.ts", "POST", "/api/documents.info")]


# --- Next.js app router --------------------------------------------------------------

ROUTE = (
    "export const dynamic = 'force-dynamic';\n"
    "export async function GET(request: Request) {}\n"
    "export function POST() {}\n"
    "export const DELETE = async () => {};\n"
    "function PUT() {}\n"
    "export function helper() {}\n"
    "export const PATCH = 5;\n"
)


def test_the_walk_records_exported_method_functions() -> None:
    assert _entries(ROUTE, "app/api/users/route.ts") == [
        ["<module>", "GET", "d", "GET", "", 2, 0, 0, "GET", "", ""],
        ["<module>", "POST", "d", "POST", "", 3, 0, 0, "POST", "", ""],
        ["<module>", "DELETE", "d", "DELETE", "", 4, 0, 0, "DELETE", "", ""],
    ]


def test_the_walk_does_not_depend_on_the_path() -> None:
    assert _entries(ROUTE, "lib/anything.ts") == _entries(ROUTE, "app/api/users/route.ts")


def test_a_method_export_inside_a_function_or_class_is_nothing() -> None:
    src = "function f() {\n  function GET() {}\n}\nclass K {\n  GET() {}\n}\n"
    assert _entries(src) == []


@pytest.mark.parametrize(
    ("rel", "want"),
    [
        ("app/api/users/route.ts", "/api/users"),
        ("app/route.ts", "/"),
        ("src/app/(collect)/p/[slug]/route.ts", "/p/{slug}"),
        ("apps/web/src/app/api/[...path]/route.js", "/api/{path}"),
        ("app/[[...slug]]/route.tsx", "/{slug}"),
        ("app/api/@modal/x/route.mjs", "/api/x"),
        ("app/api/users/[id]/posts/[postId]/route.mts", "/api/users/{id}/posts/{postId}"),
        ("app/api/users/page.tsx", None),
        ("app/api/users/route.test.ts", None),
        ("app/api/users/route.py", None),
        ("lib/route.ts", None),
        ("apps/api/route.ts", None),
        ("route.ts", None),
        ("app/a b/route.ts", None),
        ("app/`x`/route.ts", None),
        ("app/[a b]/route.ts", None),
        ("app/" + "x" * 300 + "/route.ts", None),
    ],
)
def test_route_of(rel: str, want: str | None) -> None:
    assert ts_surface_next.route_of(rel) == want


def test_a_route_file_end_to_end() -> None:
    files = {
        "src/app/api/users/[id]/route.ts": ROUTE,
        "src/app/(site)/health/route.js": "export function GET() {}\n",
        "src/lib/verbs.ts": ROUTE,
        "src/app/api/users/[id]/page.tsx": "export function GET() {}\n",
    }
    assert _linked(files) == [
        ("src/app/(site)/health/route.js", "GET", "/health"),
        ("src/app/api/users/[id]/route.ts", "DELETE", "/api/users/{id}"),
        ("src/app/api/users/[id]/route.ts", "GET", "/api/users/{id}"),
        ("src/app/api/users/[id]/route.ts", "POST", "/api/users/{id}"),
    ]


def test_a_route_file_entry_resolves_to_its_function() -> None:
    files = {"app/api/x/route.ts": "export async function GET() {\n  return helper();\n}\n"}
    sym = A.extract({k: v.encode() for k, v in files.items()}, {}).symbols
    [e] = [e for e in sym.entries if e.kind == "http"]
    assert (e.label, e.handler, e.nodes) == ("GET", "GET", (("app/api/x/route.ts", "GET"),))
