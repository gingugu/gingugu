"""What the tool call in progress arrived on: its HTTP request and session id.

The MCP SDK 2.x hands this to handlers as an argument and keeps no ambient
copy (1.x had the ``request_ctx`` ContextVar, which ``fence`` and ``session``
read). Threading a ``Context`` through every tool would put an SDK type on the
published tool signatures, so ``middleware`` records the two facts the server
needs in a ContextVar of its own for the duration of each message instead.

The handler runs inside the middleware chain, in the same task, so the value is
visible to it and reset the moment the message is done. Outside a message there
is nothing here, which callers read as "unknown".

**Session identity.** 2.x builds a fresh ``ServerSession`` for every message,
so the session object no longer names a conversation. What does:

- **stdio**: one process per client, so one id for the life of the process.
- **legacy streamable HTTP**: the ``Mcp-Session-Id`` header the SDK minted at
  ``initialize``. Stored as a digest, not verbatim, so the access log never
  holds a live session handle.
- **2026-07-28 streamable HTTP**: sessionless by design. No header, no id.
"""

from __future__ import annotations

import hashlib
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

_SESSION_HEADER = "mcp-session-id"


@dataclass(frozen=True)
class Inbound:
    """The message being handled: its HTTP request (None on stdio) and session."""

    request: Any | None
    session_id: str | None


_CURRENT: ContextVar[Inbound | None] = ContextVar("gingugu_inbound", default=None)
_PROCESS_SESSION: str | None = None


def current() -> Inbound | None:
    """The message in flight, or None outside one."""
    return _CURRENT.get()


def _process_session() -> str:
    global _PROCESS_SESSION
    if _PROCESS_SESSION is None:
        _PROCESS_SESSION = uuid.uuid4().hex
    return _PROCESS_SESSION


def _session_id(request: Any | None) -> str | None:
    if request is None:
        return _process_session()
    headers = getattr(request, "headers", None)
    raw = headers.get(_SESSION_HEADER) if headers is not None else None
    if not raw:
        return None
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def middleware(ctx, call_next):
    """``MCPServer(middleware=[...])`` entry: scope ``Inbound`` to one message."""
    request = getattr(ctx, "request", None)
    token = _CURRENT.set(Inbound(request=request, session_id=_session_id(request)))
    try:
        return await call_next(ctx)
    finally:
        _CURRENT.reset(token)
