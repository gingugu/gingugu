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
connection is not the end: a background loop re-derives, reconnects and
replays the client's ``initialize`` itself, and the client never re-handshakes.
A request the brain refused unrun - a 401 for a forgotten token, a 404 for a
forgotten session - is sent again, once. A request whose fate is unknown is
failed, never replayed. New requests wait briefly for the next link, then are
refused. Only a rejected owner token ends the proxy.

The credential vault is handled here rather than relayed: it is this machine's
keychain and a remote brain cannot serve it. With credentials enabled, the
brain's `credential_*` tools are swapped in ``tools/list`` for this machine's
(``proxy_vault``) and every ``tools/call`` for one is answered locally. With
them disabled, `credential_*` is hidden and refused.
"""

from __future__ import annotations

from pathlib import Path

import anyio
import httpx
from anyio.abc import ObjectReceiveStream, ObjectSendStream
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from mcp.types import METHOD_NOT_FOUND, JSONRPCRequest

from .proxy_auth import SessionAuth, SessionTokens
from .proxy_preflight import ProxyRefused, preflight
from .proxy_session import (
    CLIENT_GONE,
    CRED_PREFIX,
    DeriveTransient,
    Link,
    ProxyLost,
    error,
    fatal_in,
    http_status_in,
    leaf_name,
    replay_handshake,
)
from .proxy_vault import LocalVault, open_vault

__all__ = ["ProxyLost", "ProxyRefused", "preflight", "run", "serve_stdio"]

_BACKOFF_START = 0.25
_BACKOFF_CAP = 5.0
_HOLD_SECONDS = 5.0  # after a link drops, how long a new call waits for the next one
_REPLAY_MAX_AGE = 10.0  # a refused request older than this is errored, not re-sent


class _Lost(Exception):
    """A connection ended; ``established`` says whether it ever came up."""

    def __init__(self, established: bool) -> None:
        self.established = established


class _Session:
    """State shared by the client reader and the connection loop."""

    def __init__(self, url, owner_token, client_write, derive_args, scope, vault=None) -> None:
        self.vault: LocalVault | None = vault
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
        # Reused across reconnects: deriving per attempt would fill the brain's
        # live-token cap and lock every machine out.
        self.tokens = SessionTokens(url, owner_token, derive_args)
        self.lost_at = float("-inf")  # when the last live link dropped
        # Refused unrun by the last link, with when: sent on the next, if fresh.
        self.replay: list[tuple[float, SessionMessage]] = []

    async def live_link(self) -> Link | None:
        """The live link, waiting out a reconnect that started under ``_HOLD_SECONDS`` ago."""
        while self.link is None and anyio.current_time() < self.lost_at + _HOLD_SECONDS:
            await anyio.sleep(0.05)
        return self.link

    async def expire_replays(self) -> None:
        """Error the refused requests too old to send. The client may have given
        up on one and re-sent it itself, so replaying it could write twice."""
        cutoff = anyio.current_time() - _REPLAY_MAX_AGE
        stale = [item for at, item in self.replay if at < cutoff]
        self.replay = [(at, item) for at, item in self.replay if at >= cutoff]
        for item in stale:
            text = "remote brain was away too long; not sent - try again"
            await self.client_write.send(error(item.message.root.id, text))

    async def read_client(self, client_read: ObjectReceiveStream) -> None:
        """The one reader of the client for the whole run; routes to the live link."""
        await self.attempted.wait()
        try:
            async for item in client_read:
                if isinstance(item, Exception):
                    continue  # a malformed line from the client; the SDK already logged it
                msg = item.message.root
                if isinstance(msg, JSONRPCRequest):
                    called = str((msg.params or {}).get("name", ""))
                    if msg.method == "tools/call" and called.startswith(CRED_PREFIX):
                        if self.vault is not None:
                            reply = await self.vault.call(msg.id, msg.params or {})
                        else:
                            reply = error(
                                msg.id,
                                "credential tools are not available through a remote brain; "
                                "the vault stays on this machine",
                            )
                        await self.client_write.send(reply)
                        continue
                    if self.init_params is None and msg.method != "initialize":
                        # A pre-initialize probe (Claude Code's `server/discover`). The brain
                        # 400s any non-initialize request without a session, which would
                        # drop the link under the initialize that follows. Answer it here.
                        await self.client_write.send(
                            error(msg.id, f"method not found: {msg.method}", METHOD_NOT_FOUND)
                        )
                        continue
                link = await self.live_link()
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
        except CLIENT_GONE:
            pass  # the client left while a reply was on its way
        self.scope.cancel()  # client closed: end the run, from connected or reconnecting

    async def connect_once(self) -> None:
        """One connection's lifetime. Returns only via cancellation; raises ``_Lost``."""
        _, expires_in = await self.tokens.current()
        http = httpx.AsyncClient(auth=SessionAuth(self.tokens), timeout=httpx.Timeout(30, read=300))
        link: Link | None = None
        reason = "ConnectionLost"
        try:
            transport = streamable_http_client(f"{self.url}/mcp", http_client=http)
            async with http, transport as (r, w, _):
                if self.initialized:
                    self.reinits += 1
                    await replay_handshake(r, w, self.init_params, self.reinits)
                extra = self.vault.tools if self.vault is not None else []
                link = Link(self.client_write, r, w, extra_tools=extra, vault=self.vault)
                async with anyio.create_task_group() as tg:
                    tg.start_soon(link.pump, self.scope)
                    tg.start_soon(self._refresh, expires_in)
                    await self.expire_replays()
                    replay, self.replay = self.replay, []
                    for _, item in replay:
                        await link.forward(item, replay=True)
                    self.link = link
                    self.attempted.set()
        except (Exception, BaseExceptionGroup) as exc:
            if isinstance(exc, BaseExceptionGroup) and exc.split(Exception)[0] is None:
                raise  # only cancellation: not ours to swallow
            fatal = fatal_in(exc)
            if fatal is not None:
                raise fatal from None
            if http_status_in(exc) == 401:
                self.tokens.reauth = True  # e.g. the brain restarted and forgot every derived token
            reason = leaf_name(exc)
        finally:
            if self.link is not None:
                self.lost_at = anyio.current_time()
            self.link = None
        # The transport's task group is gone, so these sends are safe.
        if link is not None:
            for item in link.refused:  # never ran: the next link sends them again
                if item.message.root.id in link.replayed:
                    link.pending[item.message.root.id] = item  # refused twice: give up
                else:
                    self.replay.append((anyio.current_time(), item))
            await link.fail_pending(reason)
        # Only a link the brain actually answered on resets the backoff.
        raise _Lost(link is not None and link.answered)

    async def _refresh(self, expires_in: int) -> None:
        wait = expires_in / 2
        while True:
            await anyio.sleep(wait)
            try:
                _, expires_in = await self.tokens.derive()  # SessionAuth reads it per request
            except DeriveTransient:
                wait = 1.0  # the token is still good for a while; try again soon
                continue
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
            await self.expire_replays()
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
    vault_db: Path | None = None,
) -> None:
    """Relay until the client closes (returns) or the owner token is refused (ProxyLost).

    ``vault_db`` is this machine's DB; given, ``credential_*`` is served from it.
    """
    derive_args = {"grant": grant, "home": home, "ttl": ttl, "name": name}
    vault = await open_vault(vault_db)
    try:
        async with anyio.create_task_group() as tg:
            session = _Session(url, owner_token, client_write, derive_args, tg.cancel_scope, vault)
            tg.start_soon(session.read_client, client_read)
            tg.start_soon(session.connect_loop)
    except BaseExceptionGroup as group:
        fatal = fatal_in(group)
        if fatal is None:
            raise
        raise fatal from None
    finally:
        if vault is not None:
            vault.close()


async def serve(
    url: str,
    token: str,
    *,
    grant: str | None,
    home: str | None,
    vault_db: Path | None = None,
    stdin=None,
    stdout=None,
) -> None:
    """``run`` over the process's stdio (or the given text files, for tests).

    Returns when the client closes stdin; raises a bare ProxyLost, never one
    wrapped in the transport's task group, so the caller's exit 1 sees it.
    """
    from mcp.server.stdio import stdio_server

    try:
        async with stdio_server(stdin, stdout) as (read, write):
            # The SDK's stdout writer runs until this stream closes; left open,
            # the process outlives its client.
            async with write:
                await run(
                    url, token, read, write, grant=grant, home=home, name=home, vault_db=vault_db
                )
    except BaseExceptionGroup as group:
        fatal = fatal_in(group)
        if fatal is None:
            raise
        raise fatal from None


def serve_stdio(
    url: str, token: str, *, grant: str | None, home: str | None, vault_db: Path | None = None
) -> int:
    anyio.run(lambda: serve(url, token, grant=grant, home=home, vault_db=vault_db))
    return 0
