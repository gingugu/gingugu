"""``POST /token/derive``: trade an owner token for a short-lived persona token.

Body (JSON object, every key optional)::

    {"grant": "tyrone=write,crow=read", "home": "tyrone", "ttl": 3600, "name": "tyrone"}

No ``grant`` means full access. ``home`` is the namespace the derived token's
calls default to; a scoped grant must be able to read it. The reply is
``{"token": ..., "expires_in": ttl}``, sent no-store.

An owner token may derive anything. A scoped token - a crew mate's - may
derive only a token no wider than itself: no ``grant`` means its own grant,
a narrower one is allowed, a wider one is 403. Its derived token is named for
the scoped token whatever the body says (the name is the board's author),
carries no machine origin, and at most ``SCOPED_LIVE_MAX`` are live at once.
A derived token cannot outlive its TTL by deriving a fresh one, so it can
never call this.
"""

from __future__ import annotations

import dataclasses
import logging
import re

from starlette.requests import Request
from starlette.responses import JSONResponse

from .derived_tokens import DEFAULT_TTL, DerivedTokens
from .grants import FULL, WILDCARD, WRITE, Grant, origin_name
from .serve import OWNER, SCOPED
from .serve_tokens import parse_grant_spec

logger = logging.getLogger(__name__)

DERIVE_PATH = "/token/derive"
SCOPED_LIVE_MAX = 8  # a proxy holds one; reconnects and hooks need a few more
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NO_STORE = {"Cache-Control": "no-store"}


class _BadRequest(ValueError):
    pass


class _Wider(ValueError):
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


def _within(parent: Grant, child: Grant) -> bool:
    """Whether ``child`` grants nothing ``parent`` does not."""
    for ns, level in child.namespaces.items():
        have = parent.namespaces.get(WILDCARD) if ns == WILDCARD else parent.level(ns)
        if have is None or (level == WRITE and have != WRITE):
            return False
    return True


def _scoped_grant_from(body: dict, parent: Grant) -> Grant:
    """A scoped token's derived grant: its own, or narrower, and named for it."""
    home = _optional_name(body, "home")
    requested = _grant_from({**body, "name": None, "home": None})  # validates the grant
    namespaces = dict(parent.namespaces if body.get("grant") is None else requested.namespaces)
    child = Grant(f"derived:{parent.name}", namespaces, home=home, derived=True)
    if not _within(parent, child):
        raise _Wider("a scoped token may not derive a wider grant than its own")
    if child.home is not None and not child.can_read(child.home):
        raise _BadRequest("home must be a namespace the grant can read")
    return child


def _ttl_from(body: dict) -> int:
    ttl = body.get("ttl", DEFAULT_TTL)
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise _BadRequest("ttl must be an integer number of seconds")
    return ttl


def derive_endpoint(derived: DerivedTokens):
    """The route handler, bound to the server's derived-token store."""

    async def derive(request: Request) -> JSONResponse:
        kind = getattr(request.state, "token_kind", None)
        if kind not in (OWNER, SCOPED):
            return JSONResponse(
                {"error": "only an owner or scoped token may derive"}, status_code=403
            )
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        caller = getattr(request.state, "grant", None)
        try:
            if not isinstance(body, dict):
                raise _BadRequest("body must be a JSON object")
            ttl = _ttl_from(body)
            if kind == SCOPED:
                grant = _scoped_grant_from(body, caller)
                token = derived.mint(grant, ttl, max_alike=SCOPED_LIVE_MAX)
            else:
                grant = _grant_from(body)
                origin = origin_name(caller.name) if caller else None
                grant = dataclasses.replace(grant, origin=origin)
                token = derived.mint(grant, ttl)
        except _Wider as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:  # _BadRequest, or mint's ttl bounds
            return JSONResponse({"error": str(exc)}, status_code=400)
        except OverflowError as exc:
            return JSONResponse({"error": str(exc)}, status_code=429)
        logger.info("derived token %s (home=%s, ttl=%ds)", grant.name, grant.home, ttl)
        return JSONResponse({"token": token, "expires_in": ttl}, headers=_NO_STORE)

    return derive
