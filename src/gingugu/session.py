"""Stable per-session identity for the access log.

``access_log`` records *when* a memory was retrieved. What it could never say is
*alongside what*, because every row was written with a null ``context``. Without
a grouping key, "these two memories are retrieved together" can only be guessed
at by bucketing timestamps, which merges adjacent sessions and splits long ones.

The identity is whatever names the client's conversation on its transport, as
``inbound`` derives it: one id per process on stdio (one process per client),
the ``Mcp-Session-Id`` on streamable HTTP (one process, many clients - a
process-level id there would manufacture co-access between people who never
shared a conversation).

Reading it from a ContextVar rather than a handler argument is deliberate: a
``Context`` parameter would have to be threaded through every retrieval
handler, and the tool schema is a published surface worth leaving alone for a
bookkeeping field.

**A missing id is written as NULL, never as a placeholder.** Outside a request
(tests, CLI paths, background maintenance) there is no session, and inventing a
shared constant would group unrelated rows into one enormous false session. A
null says "unknown", which is true; a placeholder would say "these belong
together", which is not. The same goes for a 2026-07-28 HTTP client, which is
sessionless by design.
"""

from __future__ import annotations

from .inbound import current


def current_session_id() -> str | None:
    """Stable id for the MCP session serving the current request, or None."""
    inbound = current()
    return inbound.session_id if inbound is not None else None
