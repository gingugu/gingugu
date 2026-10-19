"""A machine left on http after its brain went TLS-only is told so, by name.

Before this, preflight's refusal said only "unreachable" and `remote status`
said "reachable: no" - the same words as a powered-off brain - so the fix (log
in again over https) took a diagnosis over SSH to find. The check is a TLS
handshake on the same host and port with no verification: it proves the port
speaks TLS and sends nothing but a ClientHello, never the token.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.remote_tls_fixtures
from __future__ import annotations

import socket
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gingugu import proxy, proxy_preflight, remote, remote_tls, serve_tls
from gingugu.remote import RemoteTarget
from tests.remote_tls_fixtures import TOKEN, vault  # noqa: F401 - fixtures


class _Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args) -> None:  # keep pytest output clean
        pass


def _serve(server: ThreadingHTTPServer) -> Iterator[int]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def tls_port(tmp_path) -> Iterator[int]:
    """A brain serving https on 127.0.0.1 with its own self-signed cert."""
    cert, key = tmp_path / "brain.pem", tmp_path / "brain.key"
    serve_tls.generate(cert, key, ["localhost", "127.0.0.1"])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    yield from _serve(server)


@pytest.fixture
def http_port() -> Iterator[int]:
    """A brain serving plain http."""
    yield from _serve(ThreadingHTTPServer(("127.0.0.1", 0), _Health))


@pytest.fixture
def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --- remote_tls.speaks_tls -------------------------------------------------------


def test_a_tls_port_speaks_tls_even_with_an_untrusted_cert(tls_port):
    # Self-signed and unpinned: verification would fail, the handshake does not.
    assert remote_tls.speaks_tls(f"http://127.0.0.1:{tls_port}") is True


def test_a_plain_http_port_does_not_speak_tls(http_port):
    assert remote_tls.speaks_tls(f"http://127.0.0.1:{http_port}") is False


def test_a_closed_port_does_not_speak_tls(closed_port):
    assert remote_tls.speaks_tls(f"http://127.0.0.1:{closed_port}") is False


def test_speaks_tls_never_raises_on_a_bad_host():
    assert remote_tls.speaks_tls("http://no-such-host.invalid:8765") is False


def test_speaks_tls_sends_no_token_and_no_request(monkeypatch, tls_port):
    # The handshake is the whole probe: no HTTP request, so no header to leak.
    sent: list[bytes] = []
    real = ssl.SSLSocket.send

    def spy(self, data, *args):
        sent.append(bytes(data))
        return real(self, data, *args)

    monkeypatch.setattr(ssl.SSLSocket, "send", spy)
    monkeypatch.setattr(ssl.SSLSocket, "sendall", spy)
    assert remote_tls.speaks_tls(f"http://127.0.0.1:{tls_port}") is True
    assert sent == []


# --- remote_tls.diagnose ---------------------------------------------------------


def test_diagnose_names_the_https_login_for_an_http_url_on_a_tls_port(tls_port):
    url = f"http://127.0.0.1:{tls_port}"
    why = remote_tls.diagnose(url)
    assert why is not None
    assert "speaks TLS" in why
    assert f"gingugu remote login https://127.0.0.1:{tls_port}" in why


def test_diagnose_has_nothing_to_say_for_an_http_url_on_an_http_port(http_port):
    assert remote_tls.diagnose(f"http://127.0.0.1:{http_port}") is None


def test_diagnose_has_nothing_to_say_for_an_http_url_on_a_closed_port(closed_port):
    assert remote_tls.diagnose(f"http://127.0.0.1:{closed_port}") is None


# --- where the hint surfaces -----------------------------------------------------


def test_preflight_refusal_names_tls_not_unreachable(vault, monkeypatch, tls_port):
    url = f"http://127.0.0.1:{tls_port}"
    vault[url] = TOKEN
    monkeypatch.setattr(remote, "_reachable", lambda u, t: False)
    monkeypatch.setattr(proxy_preflight, "_monotonic", iter(range(0, 1000, 20)).__next__)
    monkeypatch.setattr(proxy_preflight, "_sleep", lambda s: None)
    with pytest.raises(proxy.ProxyRefused) as err:
        proxy.preflight(RemoteTarget(url, "machine"), grant=None, credentials_enabled=False)
    message = str(err.value)
    assert "speaks TLS" in message and f"https://127.0.0.1:{tls_port}" in message
    assert TOKEN not in message


def test_status_prints_the_hint_when_an_http_brain_speaks_tls(vault, capsys, tls_port):
    url = f"http://127.0.0.1:{tls_port}"
    remote._write_setting(url)
    vault[url] = TOKEN
    assert remote.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "reachable: no" in out
    hint = [line for line in out.splitlines() if line.startswith("hint: ")]
    assert len(hint) == 1 and "speaks TLS" in hint[0]
    assert TOKEN not in out


def test_status_prints_no_hint_when_reachable(vault, capsys, http_port):
    url = f"http://127.0.0.1:{http_port}"
    remote._write_setting(url)
    vault[url] = TOKEN
    assert remote.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "reachable: yes" in out and "hint:" not in out


def test_status_prints_no_hint_for_a_brain_that_is_simply_down(vault, capsys, closed_port):
    url = f"http://127.0.0.1:{closed_port}"
    remote._write_setting(url)
    vault[url] = TOKEN
    assert remote.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "reachable: no" in out and "hint:" not in out
