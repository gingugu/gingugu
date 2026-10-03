"""``POST /token/derive``: trade an owner token for a short-lived persona token.

Body (JSON object, every key optional)::

    {"grant": "tyrone=write,crow=read", "home": "tyrone", "ttl": 3600, "name": "tyrone"}

No ``grant`` means full access. ``home`` is the namespace the derived token's
calls default to; a scoped grant must be able to read it. The reply is
``{"token": ..., "expires_in": ttl}``, sent no-store.

Only an owner token may derive. A scoped token cannot widen itself, and a
derived token cannot outlive its TTL by deriving a fresh one - so neither
can call this.
"""

from __future__ import annotations

import logging
import re

from starlette.requests import Request
from starlette.responses import JSONResponse

from .derived_tokens import DEFAULT_TTL, DerivedTokens
from .grants import FULL, WILDCARD, Grant
from .serve import OWNER
from .serve_tokens import parse_grant_spec

logger = logging.getLogger(__name__)

DERIVE_PATH = "/token/derive"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NO_STORE = {"Cache-Control": "no-store"}


class _BadRequest(ValueError):
    pass


def _optional_name(body: dict, key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise _BadRequest(f"{key} must be a name of letters, digits, '.', '_' or '-'")
    return value


def _grant_from(body: dict) -> Grant:
    spec = body.get("grant")
    home = _optional_name(body, "home")
    label = f"derived:{_optional_name(body, 'name') or home or 'client'}"
    if spec is None:
        return Grant(label, dict(FULL.namespaces), home=home, derived=True)
    if not isinstance(spec, str):
        raise _BadRequest("grant must be a string like 'ns=write,other=read'")
    try:
        namespaces = parse_grant_spec(spec)
    except ValueError as exc:
        raise _BadRequest(str(exc)) from exc
    for ns in namespaces:
        if ns != WILDCARD and not _NAME_RE.fullmatch(ns):
            raise _BadRequest(f"invalid namespace name {ns!r}")
    grant = Grant(label, namespaces, home=home, derived=True)
    if home is not None and not grant.can_read(home):
        raise _BadRequest("home must be a namespace the grant can read")
    return grant


def _ttl_from(body: dict) -> int:
    ttl = body.get("ttl", DEFAULT_TTL)
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise _BadRequest("ttl must be an integer number of seconds")
    return ttl


def derive_endpoint(derived: DerivedTokens):
    """The route handler, bound to the server's derived-token store."""

    async def derive(request: Request) -> JSONResponse:
        if getattr(request.state, "token_kind", None) != OWNER:
            return JSONResponse({"error": "only an owner token may derive"}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        try:
            if not isinstance(body, dict):
                raise _BadRequest("body must be a JSON object")
            grant, ttl = _grant_from(body), _ttl_from(body)
            token = derived.mint(grant, ttl)
        except ValueError as exc:  # _BadRequest, or mint's ttl bounds
            return JSONResponse({"error": str(exc)}, status_code=400)
        except OverflowError as exc:
            return JSONResponse({"error": str(exc)}, status_code=429)
        logger.info("derived token %s (home=%s, ttl=%ds)", grant.name, grant.home, ttl)
        return JSONResponse({"token": token, "expires_in": ttl}, headers=_NO_STORE)

    return derive
