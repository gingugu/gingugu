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
from .grants import FULL, Grant
from .secret_file import write_private
from .serve_tokens import TokenStore, default_path
from .server import build_server

logger = logging.getLogger(__name__)

_HEALTH_PATH = "/healthz"

SERVE_USAGE = """\
gingugu serve - run the MCP server over streamable HTTP

Usage:
  gingugu serve              Start the HTTP server.
  gingugu serve -h|--help    Show this help and exit.

`gingugu serve` takes no command-line arguments - every knob is an
environment variable, since the process is meant to run under a supervisor
(systemd, a container entrypoint) rather than typed by hand each time:

  MEMORY_SERVE_HOST            Host to bind (default: 127.0.0.1)
  MEMORY_SERVE_PORT            Port to bind (default: 8765)
  MEMORY_SERVE_TOKEN           The owner's Bearer token, full access
                                (default: a token persisted next to the DB,
                                generated on first run if none exists)
  MEMORY_LOG_LEVEL             Log verbosity (default: INFO)
  MEMORY_CREDENTIALS_ENABLED   Enable the credential_* tools (default: true)

Scoped tokens, each limited to named namespaces read-only or read-write, are
managed with `gingugu token add|list|revoke` and take effect without a restart.
"""


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject any request lacking a known ``Authorization: Bearer`` token.

    Two kinds of token are accepted. The server token (``MEMORY_SERVE_TOKEN``
    or the persisted ``serve_token``) is the owner's, with full access. A
    scoped token from ``tokens`` (``gingugu token add``) carries its own grant.
    The matching grant is attached as ``request.state.grant``; the MCP SDK
    hands each tool call its own HTTP request, so the tool wrapper fences the
    call to exactly the token that sent it (see ``handlers.fence``).

    The health-check path is exempt so load-balancer probes don't need a
    token. Comparison is constant-time to avoid leaking a token by timing.
    """

    def __init__(self, app, token: str, tokens: TokenStore | None = None) -> None:
        super().__init__(app)
        self._expected = f"Bearer {token}"
        self._tokens = tokens

    def _grant_for(self, header: str) -> Grant | None:
        # Bytes, not str: compare_digest raises on a non-ASCII str, and a
        # header is attacker-chosen.
        raw = header.encode("utf-8", "surrogateescape")
        if secrets.compare_digest(raw, self._expected.encode("utf-8")):
            return FULL
        scheme, _, presented = header.partition(" ")
        if self._tokens is None or scheme != "Bearer" or not presented:
            return None
        return self._tokens.resolve(presented)

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == _HEALTH_PATH:
            return PlainTextResponse("ok")
        grant = self._grant_for(request.headers.get("authorization", ""))
        if grant is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        request.state.grant = grant
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
            try:  # an older or hand-made file may be looser than owner-only
                token_path.chmod(0o600)
            except OSError:  # pragma: no cover - platform-dependent (e.g. Windows)
                pass
            logger.info("Using persisted serve token from %s", token_path)
            return existing
    token = secrets.token_urlsafe(32)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    # 0600 from the moment the file exists - never written first and narrowed after.
    write_private(token_path, token)
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
    mcp = build_server(transport="http")
    token_path = config.db_path.parent / "serve_token"
    token = _resolve_token(config.serve_token, token_path)

    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=token, tokens=TokenStore(default_path()))

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
