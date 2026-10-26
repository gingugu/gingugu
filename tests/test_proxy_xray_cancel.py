"""A cancelled ``code_xray`` call stops on this machine and is never answered."""

from __future__ import annotations

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCNotification, JSONRPCRequest

from gingugu import proxy_xray


def _call(req_id: int) -> SessionMessage:
    return SessionMessage(
        JSONRPCRequest(
            jsonrpc="2.0",
            id=req_id,
            method="tools/call",
            params={"name": "code_xray", "arguments": {"action": "overview", "path": "/x"}},
        )
    )


def _cancel(req_id: int) -> SessionMessage:
    return SessionMessage(
        JSONRPCNotification(
            jsonrpc="2.0", method="notifications/cancelled", params={"requestId": req_id}
        )
    )


async def test_cancel_stops_the_call_and_sends_nothing(monkeypatch):
    started, finished = anyio.Event(), []

    async def slow(self, args):
        started.set()
        await anyio.sleep(30)
        finished.append(args)

    monkeypatch.setattr(proxy_xray.XrayRelay, "_answer", slow)
    send, recv = anyio.create_memory_object_stream(8)
    async with anyio.create_task_group() as tg:
        relay = proxy_xray.XrayRelay("http://brain", None, send, tg)
        assert relay.take(_call(7)) is True
        await started.wait()
        assert relay.take(_cancel(7)) is True  # ours: consumed, not relayed
        with anyio.fail_after(2):
            while relay.running:
                await anyio.sleep(0.01)
    assert finished == []
    assert recv.statistics().current_buffer_used == 0


def test_a_cancel_for_another_request_is_not_taken():
    send, _ = anyio.create_memory_object_stream(1)
    relay = proxy_xray.XrayRelay("http://brain", None, send, None)
    assert relay.take(_cancel(99)) is False
