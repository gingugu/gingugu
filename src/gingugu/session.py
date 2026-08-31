"""Stable per-session identity for the access log.

``access_log`` records *when* a memory was retrieved. What it could never say is
*alongside what*, because every row was written with a null ``context``. Without
a grouping key, "these two memories are retrieved together" can only be guessed
at by bucketing timestamps, which merges adjacent sessions and splits long ones.

The identity used here is the **MCP session object itself**. That is the right
unit for both transports with no special-casing:

- **stdio** (``gingugu``) runs one process per client, so there is exactly one
  session for the life of the process.
- **streamable HTTP** (``gingugu serve``) runs one process for many clients, and
  the SDK gives each its own session. A process-level id would lump every
  client's retrievals together and manufacture co-access between people who
  never shared a conversation.

Reading it from the SDK's request ``ContextVar`` rather than a handler argument
is deliberate: a ``Context`` parameter would have to be threaded through every
retrieval handler, and the tool schema is a published surface worth leaving
alone for a bookkeeping field.

**A missing id is written as NULL, never as a placeholder.** Outside a request
(tests, CLI paths, background maintenance) there is no session, and inventing a
shared constant would group unrelated rows into one enormous false session. A
null says "unknown", which is true; a placeholder would say "these belong
together", which is not.
"""

from __future__ import annotations

import uuid
from weakref import WeakKeyDictionary

# The session object is the key, not ``id(session)``: CPython reuses an address
# once an object is collected, so raw ids would eventually merge a dead session
# with a live one. Weak keys also mean this map never keeps a session alive and
# drops its entry as soon as the connection is gone.
_SESSION_IDS: WeakKeyDictionary = WeakKeyDictionary()


def current_session_id() -> str | None:
    """Stable id for the MCP session serving the current request.

    Returns ``None`` when there is no request in flight, or when the SDK's
    internals are not shaped the way this expects. Callers store that ``None``
    verbatim.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx
    except ImportError:  # pragma: no cover - SDK always present in practice
        return None

    try:
        session = request_ctx.get().session
    except (LookupError, AttributeError):
        # LookupError: no request in flight. AttributeError: a request context
        # without a session, which the SDK does not produce today but which is
        # not worth crashing a write over.
        return None

    try:
        session_id = _SESSION_IDS.get(session)
        if session_id is None:
            session_id = uuid.uuid4().hex
            _SESSION_IDS[session] = session_id
        return session_id
    except TypeError:
        # Not weak-referenceable. Unknown beats wrong.
        return None
