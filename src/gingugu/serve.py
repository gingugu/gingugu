"""``gingugu serve`` — expose the MCP server over streamable HTTP.

Turns the same in-process server used by stdio into a network endpoint so a
hosted/central brain can be reached remotely. Access is gated by a Bearer
token (``MEMORY_SERVE_TOKEN``); if none is provided one is generated, saved
owner-only, and its path logged, so the server never starts silently open.

Streamable HTTP is the current MCP transport (it supersedes the legacy
HTTP+SSE transport) and tolerates load-balancer idle timeouts better.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from . import serve_tls
from .config import load_config
from .derived_tokens import DerivedTokens
from .grants import FULL, Grant
from .secret_file import write_private
from .serve_tokens import TokenStore, default_path
from .server import build

logger = logging.getLogger(__name__)

_HEALTH_PATH = "/healthz"
# The SDK refuses an MCP request body over 4 MiB with a 413. A whole-brain
# ``memory_import`` is one request and outgrows that, so the owner can still
# restore an export to a central brain in one call.
_MAX_BODY = 64 * 1024 * 1024
# request.state.token_kind: only an OWNER token may derive (see serve_derive).
OWNER, DERIVED, SCOPED = "owner", "derived", "scoped"

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
  MEMORY_SERVE_TLS             auto (default): https on any non-loopback
                                bind with a self-signed cert generated next
                                to the DB, plain http on loopback; on; off
  MEMORY_SERVE_TLS_CERT        Your own cert + key (PEM) instead of the
  MEMORY_SERVE_TLS_KEY          generated one; turns TLS on
  MEMORY_SERVE_TLS_SAN         Extra names for the generated cert, comma-separated
  MEMORY_LOG_LEVEL             Log verbosity (default: INFO)
  MEMORY_CREDENTIALS_ENABLED   Enable the credential_* tools (default: true)
  MEMORY_CONFIRM_DESTRUCTIVE   off skips asking the user to confirm destructive
                                tools, for automation (default: ask)

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

    def __init__(
        self,
        app,
        token: str,
        tokens: TokenStore | None = None,
        derived: DerivedTokens | None = None,
    ) -> None:
        super().__init__(app)
        self._expected = f"Bearer {token}"
        self._tokens = tokens
        self._derived = derived

    def _identify(self, header: str) -> tuple[Grant, str] | None:
        """The grant a header carries and its kind: owner, derived or scoped."""
        # Bytes, not str: compare_digest raises on a non-ASCII str, and a
        # header is attacker-chosen.
        raw = header.encode("utf-8", "surrogateescape")
        if secrets.compare_digest(raw, self._expected.encode("utf-8")):
            return FULL, OWNER
        scheme, _, presented = header.partition(" ")
        if scheme != "Bearer" or not presented:
            return None
        if self._derived is not None:
            grant = self._derived.resolve(presented)
            if grant is not None:
                return grant, DERIVED
        if self._tokens is None:
            return None
        grant = self._tokens.resolve(presented)
        if grant is None:
            return None
        # TokenStore hands out a full grant only for an owner entry.
        return grant, (OWNER if grant.is_full else SCOPED)

    def _grant_for(self, header: str) -> Grant | None:
        found = self._identify(header)
        return found[0] if found else None

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == _HEALTH_PATH:
            return PlainTextResponse("ok")
        found = self._identify(request.headers.get("authorization", ""))
        if found is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        request.state.grant, request.state.token_kind = found
        return await call_next(request)


def _resolve_token(configured: str | None, token_path: Path) -> str:
    """Resolve the Bearer token, in priority order (never silent-open):

    1. ``MEMORY_SERVE_TOKEN`` — explicit override always wins; not persisted.
    2. A token previously saved at ``token_path``.
    3. A freshly generated token, saved to ``token_path`` (owner-only); its
       path, never its value, is logged - stable across restarts, no external
       secret store.
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
    # The path, never the token: under a supervisor stderr is a journal that
    # keeps it, other groups can read, and log shipping can carry off the box.
    logger.warning(
        "No serve token found - generated the owner token at %s (0600). " "Read it with: cat %s",
        token_path,
        token_path,
    )
    return token


def mcp_app(mcp) -> Starlette:
    """The SDK's streamable-HTTP app as `gingugu serve` configures it.

    ``host`` is the real bind host, not the SDK's 127.0.0.1 default: on a
    loopback host the SDK allows only localhost Host headers (DNS-rebinding
    protection), which answered every LAN client with 421. A LAN bind relies
    on the Bearer token instead; a loopback bind keeps the check.
    """
    return mcp.streamable_http_app(host=load_config().serve_host, max_request_body_size=_MAX_BODY)


def build_app(
    mcp, token: str, tokens: TokenStore | None, derived: DerivedTokens, *, ctx=None
) -> Starlette:
    """The HTTP app `gingugu serve` runs: MCP at /mcp, token derivation, auth.

    With ``ctx`` it also serves the remote-mode hook routes (``serve_hooks``).
    """
    from .serve_derive import DERIVE_PATH, derive_endpoint

    app = mcp_app(mcp)
    app.add_route(DERIVE_PATH, derive_endpoint(derived), methods=["POST"])
    if ctx is not None:
        from .serve_hooks import hook_routes

        for path, handler in hook_routes(ctx).items():
            app.add_route(path, handler, methods=["POST"])
        from .serve_ui import ui_routes

        app.router.routes.append(ui_routes(ctx))
    app.add_middleware(BearerAuthMiddleware, token=token, tokens=tokens, derived=derived)
    return app


def serve() -> None:
    """Console entry point for ``gingugu serve``."""
    import uvicorn

    config = load_config()
    mcp, ctx = build(transport="http")
    token_path = config.db_path.parent / "serve_token"
    token = _resolve_token(config.serve_token, token_path)

    tls = serve_tls.resolve(config)
    app = build_app(mcp, token, TokenStore(default_path()), DerivedTokens(), ctx=ctx)

    logger.info(
        "gingugu serve -> %s://%s:%d/mcp (credentials_enabled=%s)",
        "https" if tls else "http",
        config.serve_host,
        config.serve_port,
        config.credentials_enabled,
    )
    _log_tls(config, tls)
    uvicorn.run(
        app,
        host=config.serve_host,
        port=config.serve_port,
        log_level=config.log_level.lower(),
        ssl_certfile=str(tls.cert) if tls else None,
        ssl_keyfile=str(tls.key) if tls else None,
    )


def _log_tls(config, tls: serve_tls.TlsFiles | None) -> None:
    """Say what a client needs, so a refused handshake never leaves anyone guessing."""
    if tls is None:
        if not serve_tls.is_loopback(config.serve_host):
            logger.warning(
                "MEMORY_SERVE_TLS=off on a network bind: tokens cross the network unencrypted"
            )
        return
    if not tls.generated:
        logger.info("TLS with the configured cert %s", tls.cert)
        return
    logger.info(
        "TLS with a self-signed cert %s (sha256 %s). `gingugu remote login` pins it for "
        "you. A client that connects to this URL directly will not trust it: give the "
        "server a cert that client trusts (MEMORY_SERVE_TLS_CERT + MEMORY_SERVE_TLS_KEY), "
        "or serve plain http with MEMORY_SERVE_TLS=off",
        tls.cert,
        serve_tls.fingerprint(tls.cert.read_text(encoding="ascii")),
    )
