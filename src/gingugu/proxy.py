"""`gingugu` in remote mode: a stdio <-> streamable-HTTP relay to the brain.

When a remote brain is active (`gingugu remote on` or MEMORY_REMOTE_URL), a
bare `gingugu` does not open the local DB. It relays MCP JSON-RPC between its
own stdio and `gingugu serve`, so every client config stays ``command: gingugu``
and nothing about the client changes. It never falls back to the local DB: if
the brain cannot be used, it refuses to start (`preflight`) or reports the loss
(`ProxyLost`).

Two things are handled here rather than relayed. The credential vault is this
machine's keychain and a remote brain cannot serve it, so `credential_*` tools
are hidden from ``tools/list`` and refused on ``tools/call``. And the transport
answers nothing for a request whose POST failed, so every in-flight request is
failed explicitly before the proxy exits. The token is never put in a message.
"""

from __future__ import annotations

import anyio
import httpx
from anyio.abc import ObjectReceiveStream, ObjectSendStream
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from mcp.types import (
    INVALID_REQUEST,
    ErrorData,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCRequest,
    JSONRPCResponse,
)

from . import remote
from .remote import RemoteTarget

_CRED_PREFIX = "credential_"
_CLIENT_GONE = (anyio.BrokenResourceError, anyio.ClosedResourceError)


class ProxyRefused(Exception):
    """Remote mode cannot start. The message is user-facing."""


class ProxyLost(Exception):
    """The remote brain connection failed or ended abnormally."""


def preflight(target: RemoteTarget, *, grant: str | None, credentials_enabled: bool) -> str:
    """Return the bearer token, or raise ProxyRefused. Config refusals come first
    so they never touch the keychain or the network."""
    if grant:
        raise ProxyRefused(
            "MEMORY_GRANT is not supported over a remote brain yet "
            "(scoped grants need derived tokens). Unset MEMORY_GRANT."
        )
    if credentials_enabled:
        raise ProxyRefused(
            "the credential vault is this machine's keychain and a remote brain "
            "cannot serve it. Set MEMORY_CREDENTIALS_ENABLED=false."
        )
    try:
        token = remote.token_for(target.url)
    except Exception as exc:  # noqa: BLE001 - any keychain failure is a refusal
        raise ProxyRefused(f"could not read the keychain ({type(exc).__name__})") from None
    if not token:
        raise ProxyRefused(f"no token in the keychain. Run: gingugu remote login {target.url}")
    if not remote._reachable(target.url, token):
        raise ProxyRefused(f"unreachable: {target.url}")
    return token


def _wrap(message: JSONRPCRequest | JSONRPCResponse | JSONRPCError) -> SessionMessage:
    return SessionMessage(JSONRPCMessage(message))


def _error(req_id: str | int, text: str, code: int = INVALID_REQUEST) -> SessionMessage:
    return _wrap(JSONRPCError(jsonrpc="2.0", id=req_id, error=ErrorData(code=code, message=text)))


def _leaf_name(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return type(exc).__name__


class _Relay:
    def __init__(self, client_read, client_write, srv_read, srv_write) -> None:
        self.client_read: ObjectReceiveStream = client_read
        self.client_write: ObjectSendStream = client_write
        self.srv_read: ObjectReceiveStream = srv_read
        self.srv_write: ObjectSendStream = srv_write
        self.pending: set[str | int] = set()
        self.list_ids: set[str | int] = set()

    async def client_to_server(self, scope: anyio.CancelScope) -> None:
        async for item in self.client_read:
            if isinstance(item, Exception):
                continue  # a malformed line from the client; the SDK already logged it
            msg = item.message.root
            if isinstance(msg, JSONRPCRequest):
                called = str((msg.params or {}).get("name", ""))
                if msg.method == "tools/call" and called.startswith(_CRED_PREFIX):
                    await self.client_write.send(
                        _error(
                            msg.id,
                            "credential tools are not available through a remote brain; "
                            "the vault stays on this machine",
                        )
                    )
                    continue
                self.pending.add(msg.id)
                if msg.method == "tools/list":
                    self.list_ids.add(msg.id)
            await self.srv_write.send(item)
        scope.cancel()  # client closed: end the relay, let the transport hang up

    async def server_to_client(self, scope: anyio.CancelScope) -> None:
        async for item in self.srv_read:
            if isinstance(item, Exception):
                raise item
            msg = item.message.root
            if isinstance(msg, JSONRPCResponse | JSONRPCError):
                self.pending.discard(msg.id)
                if isinstance(msg, JSONRPCResponse) and msg.id in self.list_ids:
                    self.list_ids.discard(msg.id)
                    tools = msg.result.get("tools")
                    if isinstance(tools, list):
                        msg.result["tools"] = [
                            t for t in tools if not str(t.get("name", "")).startswith(_CRED_PREFIX)
                        ]
            try:
                await self.client_write.send(item)
            except _CLIENT_GONE:
                scope.cancel()
                return
        raise ConnectionError("brain closed the stream")

    async def fail_pending(self, reason: str) -> None:
        text = f"remote brain connection lost: {reason}"
        for req_id in sorted(self.pending, key=str):
            try:
                await self.client_write.send(_error(req_id, text))
            except _CLIENT_GONE:
                break
        self.pending.clear()


async def run(
    url: str,
    token: str,
    client_read: ObjectReceiveStream,
    client_write: ObjectSendStream,
) -> None:
    """Relay until the client closes (returns) or the brain is lost (ProxyLost)."""
    http = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}, timeout=httpx.Timeout(30, read=300)
    )
    relay: _Relay | None = None
    try:
        async with http, streamable_http_client(f"{url}/mcp", http_client=http) as (r, w, _):
            relay = _Relay(client_read, client_write, r, w)
            async with anyio.create_task_group() as tg:
                tg.start_soon(relay.client_to_server, tg.cancel_scope)
                tg.start_soon(relay.server_to_client, tg.cancel_scope)
    except (Exception, BaseExceptionGroup) as exc:
        if isinstance(exc, BaseExceptionGroup) and exc.split(Exception)[0] is None:
            raise  # only cancellation: not ours to swallow
        reason = _leaf_name(exc)
        # The transport's task group is already gone, so these sends are safe.
        if relay is not None:
            await relay.fail_pending(reason)
        raise ProxyLost(f"remote brain connection lost: {reason}") from None


def serve_stdio(url: str, token: str) -> int:
    from mcp.server.stdio import stdio_server

    async def _main() -> None:
        async with stdio_server() as (read, write):
            await run(url, token, read, write)

    anyio.run(_main)
    return 0
