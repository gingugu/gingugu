"""Building blocks for `proxy.run`: token derivation, messages, one live link.

Kept apart from `proxy` so each file stays small. Nothing here ever puts a token
into a message or an exception.
"""

from __future__ import annotations

import anyio
import httpx
from anyio.abc import ObjectReceiveStream, ObjectSendStream
from mcp.shared.message import SessionMessage
from mcp.types import (
    INVALID_REQUEST,
    ErrorData,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)

CRED_PREFIX = "credential_"
CLIENT_GONE = (anyio.BrokenResourceError, anyio.ClosedResourceError)
_SESSION_TERMINATED = 32600  # the SDK's synthetic reply when the brain 404s a session


class ProxyLost(Exception):
    """The remote brain connection failed or ended abnormally."""


class DeriveTransient(Exception):
    """Deriving a token failed in a way worth retrying (network, 5xx)."""


class SessionTerminated(Exception):
    """The brain no longer knows this MCP session."""


async def derive(
    url: str, owner_token: str, *, grant: str | None, home: str | None, ttl: int, name: str | None
) -> tuple[str, int]:
    """Trade the owner token for a session token: ``(token, expires_in)``."""
    fields = {"grant": grant, "home": home, "ttl": ttl, "name": name}
    body = {key: value for key, value in fields.items() if value is not None}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{url}/token/derive",
                json=body,
                headers={"Authorization": f"Bearer {owner_token}"},
            )
    except httpx.HTTPError as exc:
        raise DeriveTransient(type(exc).__name__) from None
    status = resp.status_code
    if status in (401, 403):
        raise ProxyLost(
            f"remote brain {url} refused the machine token (HTTP {status}). "
            f"Run: gingugu remote login {url}"
        )
    if status >= 500 or status == 429:  # 429: the brain's live-token cap; it drains
        raise DeriveTransient(f"HTTP {status}")
    if status != 200:
        try:
            detail = str(resp.json().get("error", ""))
        except (ValueError, AttributeError):
            detail = ""
        raise ProxyLost(f"remote brain rejected the session request (HTTP {status}): {detail}")
    try:
        data = resp.json()
        return str(data["token"]), int(data["expires_in"])
    except (ValueError, KeyError, TypeError):
        raise DeriveTransient("malformed derive reply") from None


def wrap(message: JSONRPCRequest | JSONRPCResponse | JSONRPCError | JSONRPCNotification):
    return SessionMessage(JSONRPCMessage(message))


def error(req_id: str | int, text: str) -> SessionMessage:
    return wrap(
        JSONRPCError(jsonrpc="2.0", id=req_id, error=ErrorData(code=INVALID_REQUEST, message=text))
    )


def leaf_name(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return type(exc).__name__


def http_status_in(exc: BaseException) -> int | None:
    """The HTTP status of the first failed response buried in ``exc``, if any."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            found = http_status_in(inner)
            if found is not None:
                return found
    return None


def fatal_in(exc: BaseException) -> ProxyLost | None:
    """The ProxyLost buried in an exception group, if any."""
    if isinstance(exc, ProxyLost):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            found = fatal_in(inner)
            if found is not None:
                return found
    return None


async def replay_handshake(
    link_read: ObjectReceiveStream, link_write: ObjectSendStream, params: dict | None, n: int
) -> None:
    """Re-initialize a fresh session on the client's behalf; its reply is not forwarded."""
    req_id = f"gingugu-proxy-reinit-{n}"
    request = JSONRPCRequest(jsonrpc="2.0", id=req_id, method="initialize", params=params)
    await link_write.send(wrap(request))
    with anyio.fail_after(30):
        async for item in link_read:
            if isinstance(item, Exception):
                raise item
            msg = item.message.root
            if isinstance(msg, JSONRPCError) and msg.id == req_id:
                raise ConnectionError("brain refused the re-initialize")
            if isinstance(msg, JSONRPCResponse) and msg.id == req_id:
                break
        else:
            raise ConnectionError("brain closed the stream")
    note = JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized")
    await link_write.send(wrap(note))


class Link:
    """One live connection to the brain: its streams and the requests in flight."""

    def __init__(self, client_write: ObjectSendStream, srv_read, srv_write) -> None:
        self.client_write = client_write
        self.srv_read: ObjectReceiveStream = srv_read
        self.srv_write: ObjectSendStream = srv_write
        self.pending: set[str | int] = set()
        self.list_ids: set[str | int] = set()
        self.answered = False  # the brain has sent at least one message on this link

    async def forward(self, item: SessionMessage) -> None:
        """Send a client message to the brain; a request is tracked until answered."""
        msg = item.message.root
        if not isinstance(msg, JSONRPCRequest):
            await self.srv_write.send(item)
            return
        self.pending.add(msg.id)
        if msg.method == "tools/list":
            self.list_ids.add(msg.id)
        try:
            await self.srv_write.send(item)
        except CLIENT_GONE:
            if msg.id in self.pending:  # else fail_pending already answered it
                self.pending.discard(msg.id)
                await self.client_write.send(error(msg.id, _lost("ConnectionLost")))

    async def pump(self, scope: anyio.CancelScope) -> None:
        """Relay brain -> client until the connection ends (raises) or the client is gone."""
        async for item in self.srv_read:
            if isinstance(item, Exception):
                raise item
            self.answered = True
            msg = item.message.root
            if isinstance(msg, JSONRPCError) and msg.error.code == _SESSION_TERMINATED:
                raise SessionTerminated
            if isinstance(msg, JSONRPCResponse | JSONRPCError):
                self.pending.discard(msg.id)
                if isinstance(msg, JSONRPCResponse) and msg.id in self.list_ids:
                    self.list_ids.discard(msg.id)
                    tools = msg.result.get("tools")
                    if isinstance(tools, list):
                        msg.result["tools"] = [
                            t for t in tools if not str(t.get("name", "")).startswith(CRED_PREFIX)
                        ]
            try:
                await self.client_write.send(item)
            except CLIENT_GONE:
                scope.cancel()
                return
        raise ConnectionError("brain closed the stream")

    async def fail_pending(self, reason: str) -> None:
        """Error every in-flight request. Never replayed: a memory_store would write twice."""
        text = _lost(reason)
        for req_id in sorted(self.pending, key=str):
            try:
                await self.client_write.send(error(req_id, text))
            except CLIENT_GONE:
                break
        self.pending.clear()


def _lost(reason: str) -> str:
    return f"remote brain connection lost: {reason}; reconnecting"
