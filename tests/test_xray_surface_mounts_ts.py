"""X7d spec, TS/JS: routes across files - the URL, not just the file's tail.

End to end through ``typescript.ADAPTER.extract``. The walk adds ``recv`` to every
entry and ``mounts`` records (``surface_records``); the linker resolves values
(``value(rel, scope, form, x, y)``):

- ``"n"`` ``x``: a ``const``/``let``/``var`` declared in ``scope`` or a scope
  enclosing it -> ``(rel, that scope, x)``; a parameter of ``scope`` is not a
  value here (receivers that are parameters are already ``#<i>``); an import
  (``import r from "./users"``, ``import { r } from``, ``const r = require("./users")``)
  -> the exporting file's local name for that export, ``(path, "<module>", local)``.
  The default export is whatever ``export default X`` / ``module.exports = X``
  names; a named export is its own name.
- ``"a"`` ``x.y``: ``x`` a namespace import of a repo module -> that module's ``y``.
- ``"r"`` a specifier (``require("./routes")`` inline) -> that module's default.

Router-shaped: a receiver in ``surface_catalog.JS_RECEIVERS``, or one whose name
ends in ``Router``/``router``/``App``/``app`` (``usersRouter``, ``adminApp``) -
``surface_catalog.entry_kind`` takes both - or a name the same file declares from
``express()``, ``express.Router()`` or ``Router()``.

Mount sites: ``P.use("/prefix", child)`` with a string-literal first argument
(``P`` router-shaped); a call passing a router-shaped identifier to a repo
function (``register(api)``, ``routes.register(router)``) is form ``pn``/``pa``
with that argument's index and ``prefix`` "" - the function's ``#<i>`` parameter
gets the caller's router; ``app.setGlobalPrefix("api")`` is form ``g``. Entries
registered on a parameter get ``recv`` ``#<i>``.
"""

from __future__ import annotations

import json

from gingugu.xray.lang import typescript

A = typescript.ADAPTER


def _routes(files: dict[str, str]) -> dict[tuple[str, str], str]:
    sym = A.extract({k: v.encode() for k, v in files.items()}, {}).symbols
    return {(e.path, e.method): e.route for e in sym.entries}


def test_the_walk_records_round_trip() -> None:
    raw = A.parse(
        "a.ts", b'import router from "./u";\napp.use("/api", router);\nrouter.get("/x", h);\n'
    )
    assert raw["sym"]["mounts"] == [["<module>", 2, "app", "/api", "n", "router", "", 0]]
    assert raw["sym"]["entries"][0][10] == "router"
    assert A.valid(json.loads(json.dumps(raw)))


def test_express_esm_default_and_named_exports() -> None:
    files = {
        "app.ts": (
            'import express from "express";\n'
            'import users from "./routes/users";\n'
            'import { itemsRouter } from "./routes/items";\n'
            "const app = express();\n"
            'app.use("/api/users", users);\n'
            'app.use("/api/items", itemsRouter);\n'
            'app.get("/health", health);\n'
        ),
        "routes/users.ts": (
            'import { Router } from "express";\n'
            "const router = Router();\n"
            'router.get("/:id", show);\n'
            "export default router;\n"
        ),
        "routes/items.ts": (
            'import { Router } from "express";\n'
            "export const itemsRouter = Router();\n"
            'itemsRouter.post("/", create);\n'
        ),
    }
    assert _routes(files) == {
        ("app.ts", "GET"): "/health",
        ("routes/users.ts", "GET"): "/api/users/:id",
        ("routes/items.ts", "POST"): "/api/items/",
    }


def test_commonjs_require_and_module_exports() -> None:
    files = {
        "app.js": (
            'const express = require("express");\n'
            'const users = require("./users");\n'
            "const app = express();\n"
            'app.use("/v1", users);\n'
            'app.use("/v2", require("./users"));\n'
        ),
        "users.js": (
            'const express = require("express");\n'
            "const router = express.Router();\n"
            'router.get("/me", me);\n'
            "module.exports = router;\n"
        ),
    }
    assert _routes(files) == {("users.js", "GET"): "/v1/me"}  # first mount by line


def test_a_router_passed_to_a_function_takes_the_callers_prefix() -> None:
    files = {
        "app.ts": (
            'import express from "express";\n'
            'import { register } from "./routes";\n'
            "const app = express();\n"
            "const api = express.Router();\n"
            'app.use("/api", api);\n'
            "register(api);\n"
        ),
        "routes.ts": ('export function register(router) {\n  router.get("/ping", ping);\n}\n'),
    }
    assert _routes(files) == {("routes.ts", "GET"): "/api/ping"}


def test_nest_global_prefix_applies_to_its_own_app_only() -> None:
    files = {
        "cats/src/main.ts": (
            "async function bootstrap() {\n"
            "  const app = await NestFactory.create(AppModule);\n"
            '  app.setGlobalPrefix("api");\n'
            "}\n"
        ),
        "cats/src/cats.controller.ts": (
            '@Controller("cats")\nexport class CatsController {\n'
            '  @Get(":id")\n  findOne() {}\n}\n'
        ),
        "dogs/src/dogs.controller.ts": (
            '@Controller("dogs")\nexport class DogsController {\n  @Get()\n  findAll() {}\n}\n'
        ),
    }
    assert _routes(files) == {
        ("cats/src/cats.controller.ts", "GET"): "/api/cats/:id",
        ("dogs/src/dogs.controller.ts", "GET"): "/dogs",
    }


def test_an_unresolvable_mount_changes_nothing() -> None:
    files = {
        "app.ts": (
            'import ext from "some-package";\n'
            'app.use("/x", ext);\n'
            "const router = express.Router();\n"
            'router.get("/own", h);\n'
        )
    }
    assert _routes(files) == {("app.ts", "GET"): "/own"}


def test_router_shaped_receivers_are_route_calls() -> None:
    files = {
        "a.ts": (
            'adminRouter.get("/a", a);\n'
            'webApp.post("/b", b);\n'
            'client.get("/c", cfg);\n'  # not router-shaped: an HTTP client call
            'appointments.get("/d", d);\n'  # "app" is a prefix here, not a suffix
        )
    }
    assert _routes(files) == {("a.ts", "GET"): "/a", ("a.ts", "POST"): "/b"}
