"""Which brain errors mean "this session is gone" - and which are just errors.

SDK 2.x reports a 404 for a forgotten session as ``-32600`` (it was the
positive literal ``32600`` in 1.x). ``-32600`` is also JSON-RPC's ordinary
"invalid request", so the code alone must not tear the link down: the words
decide.
"""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import INVALID_REQUEST, ErrorData, JSONRPCError, JSONRPCRequest

from gingugu.proxy_session import Link, SessionTerminated


def _link():
    to_client, from_link = anyio.create_memory_object_stream(8)
    srv_write, _ = anyio.create_memory_object_stream(8)
    _, srv_read = anyio.create_memory_object_stream(8)
    return Link(to_client, srv_read, srv_write), from_link


def _pending(link: Link, req_id: int) -> None:
    req = JSONRPCRequest(jsonrpc="2.0", id=req_id, method="tools/call", params={"name": "x"})
    link.pending[req_id] = SessionMessage(req)


def _error(req_id: int, text: str) -> SessionMessage:
    error = ErrorData(code=INVALID_REQUEST, message=text)
    return SessionMessage(JSONRPCError(jsonrpc="2.0", id=req_id, error=error))


async def test_an_ordinary_invalid_request_is_relayed_not_treated_as_a_lost_session():
    link, from_link = _link()
    _pending(link, 7)
    assert await link._relay(_error(7, "Invalid request parameters"), anyio.CancelScope())
    relayed = from_link.receive_nowait().message
    assert isinstance(relayed, JSONRPCError) and relayed.id == 7
    assert link.refused == [] and 7 not in link.pending


@pytest.mark.parametrize(
    "text",
    [
        "Session terminated",
        "Session not found",
        "Not Found: Session has been terminated",
        "Not Found: Invalid or expired session ID",
    ],
)
async def test_a_forgotten_session_queues_the_request_to_send_again(text):
    """The SDK client's own stand-in, and every session-404 body a brain sends."""
    link, from_link = _link()
    _pending(link, 7)
    assert await link._relay(_error(7, text), anyio.CancelScope())
    assert [item.message.id for item in link.refused] == [7]
    with pytest.raises(anyio.WouldBlock):
        from_link.receive_nowait()  # nothing reached the client: it will be replayed


async def test_a_lost_session_reply_to_nothing_in_flight_ends_the_link():
    link, _ = _link()
    with pytest.raises(SessionTerminated):
        await link._relay(_error(99, "Session not found"), anyio.CancelScope())
