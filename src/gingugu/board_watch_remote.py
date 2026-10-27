"""``gingugu board watch`` against a remote brain: pushed, not polled.

The watch derives a session token named for the persona, exactly as the proxy
does, so the brain reads the inbox as that agent's. It holds
``subscriptions/listen`` on the persona's inbox and on broadcasts, and on each
update refetches the inbox through the ``board`` tool; the emitter drops what
it has already printed. Subscribing happens before the first fetch, so a post
landing in between still wakes it.

A dropped stream has no replay, by the spec: the watch reconnects with
backoff and refetches, which is exactly what replay would have bought. A
refused machine token is fatal (``ProxyLost``), like it is for the proxy.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TextIO

import anyio

from . import board
from .proxy_auth import SessionTokens, brain_client
from .proxy_session import ProxyLost

_BACKOFF_START, _BACKOFF_CAP = 1.0, 30.0
# An idle watch re-reads the inbox this often. A routed reply becomes visible to
# the persona's other sessions when its grace runs out, and nothing is written
# then, so no update arrives: without a timer that mail would wait for some
# unrelated post to wake the watch.
REFRESH_S = 300.0


async def _fetch(client, emitter, err: TextIO) -> int:
    result = await client.call_tool("board", {"action": "inbox", "limit": board.MAX_LIMIT})
    try:
        data = json.loads(result.content[0].text)
    except (ValueError, IndexError, AttributeError):
        print("gingugu board watch: unreadable inbox reply", file=err)
        return 0
    if not data.get("ok"):
        print(f"gingugu board watch: {data.get('error', 'inbox failed')}", file=err)
        return 0
    # Off the loop: an --exec can take a while, and the listen stream and the
    # token renewal must keep running meanwhile.
    return await anyio.to_thread.run_sync(emitter.emit, data.get("posts", []))


async def watch_remote(
    url: str,
    owner_token: str,
    me: str,
    emitter,
    *,
    once: bool = False,
    grant: str | None = None,
    home: str | None = None,
    on_ready: Callable[[], None] | None = None,
    err: TextIO | None = None,
) -> None:
    """Watch ``me``'s inbox on the brain at ``url`` until cancelled (or one post, with ``once``)."""
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    err = err or sys.stderr
    tokens = SessionTokens(
        url, owner_token, {"grant": grant, "home": home, "ttl": 3600, "name": me}
    )
    uris = [board.inbox_uri(me), board.inbox_uri(board.BROADCAST)]
    delay = _BACKOFF_START
    while True:
        try:
            await tokens.current()
            async with brain_client(url, tokens) as http:
                transport = streamable_http_client(f"{url}/mcp", http_client=http)
                async with Client(transport) as client:
                    async with client.listen(resource_subscriptions=uris) as sub:
                        if await _fetch(client, emitter, err) and once:
                            return
                        if on_ready is not None:
                            on_ready()
                        delay = _BACKOFF_START
                        events = sub.__aiter__()
                        while True:
                            with anyio.move_on_after(REFRESH_S):
                                try:
                                    await events.__anext__()
                                except StopAsyncIteration:
                                    break
                            if await _fetch(client, emitter, err) and once:
                                return
        except ProxyLost:
            raise
        except Exception as exc:  # a drop, a restart, a network blip: reconnect and refetch
            print(
                f"gingugu board watch: {type(exc).__name__}: reconnecting in {delay:g}s", file=err
            )
        await anyio.sleep(delay)
        delay = min(delay * 2, _BACKOFF_CAP)
