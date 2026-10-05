"""`gingugu` in remote mode over its real stdio transport.

``test_proxy`` drives ``proxy.run`` on memory streams; these drive ``proxy.serve``,
which wraps ``run`` in the MCP SDK's ``stdio_server``. An MCP client shuts a stdio
server down by closing its stdin, and the process must then exit - not linger
holding a brain session.
"""

from __future__ import annotations

import io
import os
import time

import anyio
import pytest

from gingugu import proxy
from tests.proxy_fixtures import make_brain, serving

INIT = (
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":'
    '"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n'
)


@pytest.fixture
def brain(tmp_path, monkeypatch):
    return make_brain(tmp_path, monkeypatch)


async def test_stdin_eof_ends_the_stdio_proxy(brain):
    stdin = anyio.wrap_file(io.StringIO(INIT))
    stdout = anyio.wrap_file(io.StringIO())
    async with serving(brain.app()) as served:
        with anyio.fail_after(15):
            await proxy.serve(
                served.url, brain.machine, grant=None, home=None, stdin=stdin, stdout=stdout
            )


async def test_a_refused_token_ends_the_stdio_proxy_while_stdin_stays_open(brain):
    # The client is still attached, so the SDK's stdin reader sits in a blocking
    # read. A refused owner token must still end the proxy (exit 1), not wait
    # for the client's next line.
    read_fd, write_fd = os.pipe()
    os.write(write_fd, INIT.encode())
    stdin = anyio.wrap_file(open(read_fd, encoding="utf-8"))  # noqa: SIM115 - closed below
    stdout = anyio.wrap_file(io.StringIO())
    elapsed = None
    try:
        async with serving(brain.app()) as served:
            async with anyio.create_task_group() as tg:

                async def unstick() -> None:
                    # A blocked thread read cannot be cancelled, so a hang would also
                    # hang this test; closing the pipe ends it, and the time tells.
                    await anyio.sleep(10)
                    os.close(write_fd)

                tg.start_soon(unstick)
                started = time.monotonic()
                with pytest.raises(proxy.ProxyLost):
                    await proxy.serve(
                        served.url,
                        "not-the-token",
                        grant=None,
                        home=None,
                        stdin=stdin,
                        stdout=stdout,
                    )
                elapsed = time.monotonic() - started
                tg.cancel_scope.cancel()
    finally:
        try:
            os.close(write_fd)
        except OSError:
            pass  # already closed by unstick
        await stdin.aclose()
    assert elapsed is not None and elapsed < 8, f"proxy lingered {elapsed:.1f}s"
