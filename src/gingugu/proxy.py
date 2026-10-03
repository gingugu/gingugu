"""`gingugu` in remote mode: a stdio <-> streamable-HTTP relay to the brain.

When a remote brain is active (`gingugu remote on` or MEMORY_REMOTE_URL), a
bare `gingugu` does not open the local DB. It relays MCP JSON-RPC between its
own stdio and `gingugu serve`, so every client config stays ``command: gingugu``
and nothing about the client changes. It never falls back to the local DB: if
the brain cannot be used, it refuses to start (`preflight`) or reports the loss
(`ProxyLost`).

The keychain holds the machine's owner token, which is only ever sent to
``/token/derive``. The proxy trades it for a short-lived session token carrying
the client's MEMORY_GRANT and home namespace, uses that on ``/mcp``, and
refreshes it at half its TTL.

A client such as ChatGPT desktop keeps one process for days, so a lost
connection is not the end: in-flight requests are failed (never replayed),
new ones are refused fast, and a background loop re-derives, reconnects and
replays the client's ``initialize`` itself. The client never re-handshakes.
Only a rejected owner token ends the proxy.

Two things are handled here rather than relayed. The credential vault is this
machine's keychain and a remote brain cannot serve it, so `credential_*` tools
are hidden from ``tools/list`` and refused on ``tools/call``.
"""

from __future__ import annotations

import anyio
import httpx
from anyio.abc import ObjectReceiveStream, ObjectSendStream
from mcp.client.streamable_http import streamable_http_client
from mcp.types import JSONRPCRequest

from . import remote
from .handlers.fence import stdio_grant
from .proxy_session import (
    CRED_PREFIX,
    DeriveTransient,
    Link,
    ProxyLost,
    derive,
    error,
    fatal_in,
    leaf_name,
    replay_handshake,
)
from .remote import RemoteTarget

__all__ = ["ProxyLost", "ProxyRefused", "preflight", "run", "serve_stdio"]

_BACKOFF_START = 0.25
_BACKOFF_CAP = 5.0


class ProxyRefused(Exception):
    """Remote mode cannot start. The message is user-facing."""


def preflight(target: RemoteTarget, *, grant: str | None, credentials_enabled: bool) -> str:
    """Return the owner token, or raise ProxyRefused. Config refusals come first
    so they never touch the keychain or the network."""
    if grant:
        try:
            stdio_grant(grant)  # same rules as stdio; the brain enforces it for real
        except ValueError as exc:
            raise ProxyRefused(f"MEMORY_GRANT: {exc}") from None
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


class _Lost(Exception):
    """A connection ended; ``established`` says whether it ever came up."""

    def __init__(self, established: bool) -> None:
        self.established = established


class _Session:
    """State shared by the client reader and the connection loop."""

    def __init__(self, url, owner_token, client_write, derive_args, scope) -> None:
        self.url = url
        self.owner_token = owner_token
        self.client_write: ObjectSendStream = client_write
        self.derive_args: dict = derive_args
        self.scope: anyio.CancelScope = scope
        self.link: Link | None = None
        self.init_params: dict | None = None
        self.initialized = False
        self.attempted = anyio.Event()  # first connection attempt has resolved
        self.reinits = 0

    async def derive(self) -> tuple[str, int]:
        return await derive(self.url, self.owner_token, **self.derive_args)

    async def read_client(self, client_read: ObjectReceiveStream) -> None:
        """The one reader of the client for the whole run; routes to the live link."""
        await self.attempted.wait()
        async for item in client_read:
            if isinstance(item, Exception):
                continue  # a malformed line from the client; the SDK already logged it
            msg = item.message.root
            if isinstance(msg, JSONRPCRequest):
                called = str((msg.params or {}).get("name", ""))
                if msg.method == "tools/call" and called.startswith(CRED_PREFIX):
                    await self.client_write.send(
                        error(
                            msg.id,
                            "credential tools are not available through a remote brain; "
                            "the vault stays on this machine",
                        )
                    )
                    continue
            link = self.link
            if link is None:
                if isinstance(msg, JSONRPCRequest):
                    await self.client_write.send(
                        error(msg.id, "remote brain unavailable, reconnecting")
                    )
                continue  # a notification has nobody to tell
            if isinstance(msg, JSONRPCRequest) and msg.method == "initialize":
                self.init_params = msg.params
            elif getattr(msg, "method", None) == "notifications/initialized":
                self.initialized = True
            await link.forward(item)
        self.scope.cancel()  # client closed: end the run, from connected or reconnecting

    async def connect_once(self) -> None:
        """One connection's lifetime. Returns only via cancellation; raises ``_Lost``."""
        token, expires_in = await self.derive()
        http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=httpx.Timeout(30, read=300)
        )
        link: Link | None = None
        reason = "ConnectionLost"
        try:
            transport = streamable_http_client(f"{self.url}/mcp", http_client=http)
            async with http, transport as (r, w, _):
                if self.initialized:
                    self.reinits += 1
                    await replay_handshake(r, w, self.init_params, self.reinits)
                link = Link(self.client_write, r, w)
                self.link = link
                self.attempted.set()
                async with anyio.create_task_group() as tg:
                    tg.start_soon(link.pump, self.scope)
                    tg.start_soon(self._refresh, http, expires_in)
        except (Exception, BaseExceptionGroup) as exc:
            if isinstance(exc, BaseExceptionGroup) and exc.split(Exception)[0] is None:
                raise  # only cancellation: not ours to swallow
            fatal = fatal_in(exc)
            if fatal is not None:
                raise fatal from None
            reason = leaf_name(exc)
        finally:
            self.link = None
        # The transport's task group is gone, so these sends are safe.
        if link is not None:
            await link.fail_pending(reason)
        raise _Lost(link is not None)

    async def _refresh(self, http: httpx.AsyncClient, expires_in: int) -> None:
        wait = expires_in / 2
        while True:
            await anyio.sleep(wait)
            try:
                token, expires_in = await self.derive()
            except DeriveTransient:
                wait = 1.0  # the token is still good for a while; try again soon
                continue
            http.headers["Authorization"] = f"Bearer {token}"
            wait = expires_in / 2

    async def connect_loop(self) -> None:
        delay = _BACKOFF_START
        while True:
            try:
                await self.connect_once()
            except DeriveTransient:
                pass
            except _Lost as lost:
                if lost.established:
                    delay = _BACKOFF_START
            self.attempted.set()
            await anyio.sleep(delay)
            delay = min(delay * 2, _BACKOFF_CAP)


async def run(
    url: str,
    owner_token: str,
    client_read: ObjectReceiveStream,
    client_write: ObjectSendStream,
    *,
    grant: str | None = None,
    home: str | None = None,
    ttl: int = 3600,
    name: str | None = None,
) -> None:
    """Relay until the client closes (returns) or the owner token is refused (ProxyLost)."""
    derive_args = {"grant": grant, "home": home, "ttl": ttl, "name": name}
    try:
        async with anyio.create_task_group() as tg:
            session = _Session(url, owner_token, client_write, derive_args, tg.cancel_scope)
            tg.start_soon(session.read_client, client_read)
            tg.start_soon(session.connect_loop)
    except BaseExceptionGroup as group:
        fatal = fatal_in(group)
        if fatal is None:
            raise
        raise fatal from None


def serve_stdio(url: str, token: str, *, grant: str | None, home: str | None) -> int:
    from mcp.server.stdio import stdio_server

    async def _main() -> None:
        async with stdio_server() as (read, write):
            await run(url, token, read, write, grant=grant, home=home, name=home)

    anyio.run(_main)
    return 0
