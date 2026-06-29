"""``gingugu serve`` — expose the MCP server over streamable HTTP.

Turns the same in-process server used by stdio into a network endpoint so a
hosted/central brain can be reached remotely. Access is gated by a Bearer
token (``MEMORY_SERVE_TOKEN``); if none is provided one is generated and
announced on stderr so the server never starts silently open.

Streamable HTTP is the current MCP transport (it supersedes the legacy
HTTP+SSE transport) and tolerates load-balancer idle timeouts better.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from .config import load_config
from .server import build_server

logger = logging.getLogger(__name__)

_HEALTH_PATH = "/healthz"


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject any request lacking a matching ``Authorization: Bearer`` header.

    The health-check path is exempt so load-balancer probes don't need the
    token. Comparison is constant-time to avoid leaking the token by timing.
    """

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._expected = f"Bearer {token}"

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == _HEALTH_PATH:
            return PlainTextResponse("ok")
        provided = request.headers.get("authorization", "")
        if not secrets.compare_digest(provided, self._expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def _resolve_token(configured: str | None, token_path: Path) -> str:
    """Resolve the Bearer token, in priority order (never silent-open):

    1. ``MEMORY_SERVE_TOKEN`` — explicit override always wins; not persisted.
    2. A token previously saved at ``token_path``.
    3. A freshly generated token, saved to ``token_path`` (owner-only) and
       announced on stderr — stable across restarts, no external secret store.
    """
    if configured:
        return configured
    if token_path.exists():
        existing = token_path.read_text(encoding="utf-8").strip()
        if existing:
            logger.info("Using persisted serve token from %s", token_path)
            return existing
    token = secrets.token_urlsafe(32)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:  # pragma: no cover - platform-dependent (e.g. Windows)
        pass
    logger.warning(
        "No serve token found — generated one and saved it to %s:\n" "    Authorization: Bearer %s",
        token_path,
        token,
    )
    return token


def serve() -> None:
    """Console entry point for ``gingugu serve``."""
    import uvicorn

    config = load_config()
    mcp = build_server()
    token_path = config.db_path.parent / "serve_token"
    token = _resolve_token(config.serve_token, token_path)

    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=token)

    logger.info(
        "gingugu serve -> http://%s:%d/mcp (credentials_enabled=%s)",
        config.serve_host,
        config.serve_port,
        config.credentials_enabled,
    )
    uvicorn.run(
        app,
        host=config.serve_host,
        port=config.serve_port,
        log_level=config.log_level.lower(),
    )
