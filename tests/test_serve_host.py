"""`gingugu serve` must answer clients that reach it by a LAN name.

The MCP SDK turns on DNS-rebinding protection (Host allowlist of localhost
only) whenever FastMCP is built with a loopback host, which is its default.
Built without the real bind host, a server on 0.0.0.0 answered every remote
client with 421 Misdirected Request before auth ran. A loopback bind keeps
the protection; a LAN bind relies on the Bearer token instead.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "host-test", "version": "0"},
    },
}
_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def _post_init(tmp_path, monkeypatch, bind: str | None, host_header: str) -> int:
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "host.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "host-test")
    if bind is None:
        monkeypatch.delenv("MEMORY_SERVE_HOST", raising=False)
    else:
        monkeypatch.setenv("MEMORY_SERVE_HOST", bind)
    from gingugu.server import build_server

    app = build_server(transport="http").streamable_http_app()
    with TestClient(app, base_url=f"http://{host_header}") as client:
        return client.post("/mcp", json=_INIT, headers=_HEADERS).status_code


@pytest.mark.parametrize("host_header", ["brain.local:8765", "192.168.1.50:8765"])
def test_lan_bind_answers_a_lan_host(tmp_path, monkeypatch, host_header):
    assert _post_init(tmp_path, monkeypatch, "0.0.0.0", host_header) == 200


def test_loopback_bind_still_rejects_a_foreign_host(tmp_path, monkeypatch):
    assert _post_init(tmp_path, monkeypatch, None, "evil.example:8765") == 421


def test_loopback_bind_answers_localhost(tmp_path, monkeypatch):
    assert _post_init(tmp_path, monkeypatch, None, "localhost:8765") == 200
