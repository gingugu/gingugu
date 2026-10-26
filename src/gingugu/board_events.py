"""The brain's ``subscriptions/listen`` bus, with a synchronous way in.

The SDK's ``SubscriptionBus.publish`` is async, but every gingugu tool body
runs synchronously on the event loop (see ``handlers._HeartbeatMCP``), so a
handler that has just committed a post has no way to await it. Listeners are
synchronous by the bus contract and are invoked on the loop either way, so
``emit`` simply does the fan-out ``publish`` would have done, without the
trailing checkpoint - the listen streams drain once the tool call returns.

One instance per server: ``MCPServer(subscriptions=bus)`` serves listen
streams from it, and the board handler emits into it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import anyio.lowlevel
from mcp.shared.subscriptions import ServerEvent

logger = logging.getLogger(__name__)


class BoardBus:
    """In-process ``SubscriptionBus`` that can also be fed from sync code."""

    def __init__(self) -> None:
        self._listeners: dict[object, Callable[[ServerEvent], None]] = {}

    def emit(self, event: ServerEvent) -> None:
        """Deliver ``event`` to every listener now; one raising never starves the rest."""
        for listener in list(self._listeners.values()):
            try:
                listener(event)
            except Exception:  # fan-out boundary, as in the SDK's own bus
                logger.exception("subscription listener raised; continuing")

    async def publish(self, event: ServerEvent) -> None:
        self.emit(event)
        await anyio.lowlevel.checkpoint()

    def subscribe(self, listener: Callable[[ServerEvent], None]) -> Callable[[], None]:
        token = object()
        self._listeners[token] = listener

        def unsubscribe() -> None:
            self._listeners.pop(token, None)

        return unsubscribe
