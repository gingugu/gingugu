"""The client's ``MEMORY_GRANT``, as the hooks apply it.

A client whose MCP calls are fenced (the proxy derives its token from this
grant) must have its hooks fenced the same way, or involuntary recall,
tripwires and minion warm-up reach past the fence: they pick namespaces from
crow, the persona and the cwd, and in remote mode ask the brain on the owner
token. So each hook narrows what it looks up to what this grant can read.

Unset means unfenced, as on stdio. A grant that cannot be honoured (empty,
malformed, ``*=write``) is never read as "no fence": callers treat it as
nothing readable, and the hook stays quiet.
"""

from __future__ import annotations

import os

from .grants import WILDCARD, Grant


def client_grant() -> Grant | None:
    """The client's grant, or None when ``MEMORY_GRANT`` is unset.

    Raises ValueError when it is set but cannot be honoured.
    """
    spec = os.environ.get("MEMORY_GRANT")
    if spec is None:
        return None
    from .handlers.fence import stdio_grant

    return stdio_grant(spec)


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
