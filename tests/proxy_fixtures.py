"""Shared rig for the remote-mode proxy suites.

A real `gingugu serve` app (``serve.build_app``) on a loopback socket, with an
ASGI recorder of every request's path and Authorization header, and a
ClientSession whose transport is ``proxy.run`` in place of stdio.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from dataclasses import dataclass, field

import anyio
from mcp import ClientSession

from gingugu import proxy
from gingugu.derived_tokens import DerivedTokens
from gingugu.serve_tokens import TokenStore
from tests.test_embeddings import FakeEmbedder

SERVE_TOKEN = "proxy-serve-token-" + "x" * 30


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def payload(result) -> dict:
    return json.loads(result.content[0].text)


@dataclass
class Brain:
    tmp_path: object
    machine: str  # a TokenStore owner token, as `gingugu remote login` mints
    seen: list[tuple[str, str]] = field(default_factory=list)
    derived: DerivedTokens = field(default_factory=DerivedTokens)

    def app(self, *, fresh_derived: bool = False):
        """A new app over the same DB; ``fresh_derived`` simulates a restart."""
        from gingugu.serve import build_app
        from gingugu.server import build_server

        if fresh_derived:
            self.derived = DerivedTokens()
        inner = build_app(
            build_server(transport="http"),
            SERVE_TOKEN,
            TokenStore(self.tmp_path / "serve_tokens.json"),
            self.derived,
        )
        seen = self.seen

        async def recording(scope, receive, send):
            if scope["type"] == "http":
                headers = dict(scope["headers"])
                seen.append((scope["path"], headers.get(b"authorization", b"").decode()))
            await inner(scope, receive, send)

        return recording

    def mcp_auth(self) -> list[str]:
        return [auth for path, auth in self.seen if path.startswith("/mcp")]


def make_brain(tmp_path, monkeypatch, *, credentials: bool = True) -> Brain:
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "brain.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "server-default")
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "true" if credentials else "false")
    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    machine = TokenStore(tmp_path / "serve_tokens.json").add_owner("macbookpro")
    return Brain(tmp_path=tmp_path, machine=machine)


class Served:
    def __init__(self, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self._uv = None
        self._task = None

    async def start(self, app) -> None:
        import uvicorn

        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self._uv = uvicorn.Server(config)
        self._task = asyncio.create_task(self._uv.serve())
        while not self._uv.started:
            if self._task.done():
                self._task.result()  # surface a bind failure
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._uv.should_exit = True
        self._uv.force_exit = True
        await self._task
        # A stopped uvicorn leaves sse-starlette's process-global exit flag set.
        from sse_starlette.sse import AppStatus

        AppStatus.should_exit = False


@contextlib.asynccontextmanager
async def serving(app):
    served = Served(free_port())
    await served.start(app)
    try:
        yield served
    finally:
        await served.stop()


@contextlib.asynccontextmanager
async def through_proxy(url: str, owner: str, **kwargs):
    """A ClientSession whose transport is ``proxy.run``; yields (session, outcome).

    ``outcome`` records what ``proxy.run`` returned or raised once it ends.
    """
    c2p_send, c2p_recv = anyio.create_memory_object_stream(16)
    p2c_send, p2c_recv = anyio.create_memory_object_stream(16)
    outcome: dict = {}

    async def _run() -> None:
        try:
            outcome["returned"] = await proxy.run(url, owner, c2p_recv, p2c_send, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            outcome["raised"] = exc
        finally:
            await p2c_send.aclose()

    async with anyio.create_task_group() as tg:
        tg.start_soon(_run)
        async with ClientSession(p2c_recv, c2p_send) as session:
            yield session, outcome
        await c2p_send.aclose()
