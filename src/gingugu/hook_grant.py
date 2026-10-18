"""The client's ``MEMORY_GRANT``, as the hooks apply it.

A client whose MCP calls are fenced (the proxy derives its token from this
grant) must have its hooks fenced the same way, or involuntary recall,
tripwires and minion warm-up reach past the fence: they pick namespaces from
crow, the persona and the cwd, and in remote mode ask the brain on the owner
token. So each hook narrows what it looks up to what this grant can read.

The grant is the one the client's gingugu servers published (see
``client_grants``), so a ``MEMORY_GRANT`` set only in the MCP ``env`` block
fences the hooks too. The hook's own ``MEMORY_GRANT``, if set, must agree with
it, and the client's servers must agree with each other.

Unset everywhere means unfenced, as on stdio. A grant that cannot be honoured
(empty, malformed, ``*=write``, or a disagreement) is never read as "no fence":
callers treat it as nothing readable, and the hook stays quiet.
"""

from __future__ import annotations

import logging
import os

from .grants import WILDCARD, Grant

logger = logging.getLogger(__name__)


def _parse(spec: str | None) -> Grant | None:
    from .handlers.fence import stdio_grant

    return None if spec is None else stdio_grant(spec)


def _key(grant: Grant | None) -> tuple | None:
    return None if grant is None else tuple(sorted(grant.namespaces.items()))


def client_grant() -> Grant | None:
    """The client's grant, or None when it runs unfenced.

    Raises ValueError when a grant is set but cannot be honoured, or when the
    hook's own grant and its client's servers' grants disagree.
    """
    from . import client_grants

    own = os.environ.get("MEMORY_GRANT")
    try:
        specs = client_grants.client_publications()
        if specs is None:
            return _parse(own)
        grants = [_parse(spec) for spec in specs]
        if own is not None:
            grants.append(_parse(own))
    except ValueError as exc:
        logger.warning("gingugu hooks stay quiet: %s", exc)
        raise
    if len({_key(g) for g in grants}) > 1:
        seen = sorted({repr(s) for s in specs} | ({repr(own)} if own is not None else set()))
        msg = f"the client's grants disagree ({', '.join(seen)}); set MEMORY_GRANT in one place"
        logger.warning("gingugu hooks stay quiet: %s", msg)
        raise ValueError(msg)
    return grants[0]


def narrow_spec(spec: str) -> str | None:
    """A minion's agent-file grant, cut down to what the client can read.

    A ``*`` entry is kept: the warm-up confines it to the client's namespace
    list, which ``namespaces_for`` has already narrowed. None means the minion
    gets no warm-up.
    """
    from .serve_tokens import parse_grant_spec

    try:
        client = client_grant()
        if client is None:
            return spec
        minion = parse_grant_spec(spec)
    except ValueError:
        return None
    kept = [f"{n}={lvl}" for n, lvl in minion.items() if n == WILDCARD or client.can_read(n)]
    return ",".join(kept) or None
