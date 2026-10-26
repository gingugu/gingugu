"""``/ui/api/*``: the Memory Explorer's read API on a remote brain.

GET only, owner tokens only (like the hook routes): the Explorer shows the whole
brain, so a scoped token has no business here. The work is the same
``ui_api.dispatch`` the local ``gingugu ui`` process runs, over this brain's own
DB on a read-only connection, in a worker thread so it never blocks the loop.
"""

from __future__ import annotations

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .serve import OWNER
from .ui_api import UiEnv, dispatch

UI_PREFIX = "/ui/api/"


def ui_routes(ctx) -> Route:
    """The one catch-all GET route, bound to the server's context."""

    async def handle(request: Request) -> JSONResponse:
        if getattr(request.state, "token_kind", None) != OWNER:
            return JSONResponse(
                {"ok": False, "error": "only an owner token may use the Explorer API"}, 403
            )
        env = UiEnv(
            db_path=ctx.config.db_path,
            embedder=lambda: ctx.store.embedder,
            weights=ctx.config.weights,
            decay_lambda=ctx.config.decay_lambda,
        )
        path = "/" + request.path_params["rest"]
        params = dict(request.query_params)
        status, body = await anyio.to_thread.run_sync(dispatch, path, params, env)
        return JSONResponse(body, status_code=status)

    return Route(UI_PREFIX + "{rest:path}", handle, methods=["GET"])
