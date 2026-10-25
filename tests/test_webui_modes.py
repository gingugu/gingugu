"""`gingugu ui` just knows which brain it is looking at.

Per request it asks ``remote.active()`` - the same resolver the hooks and the
MCP proxy use - and either answers ``/api/*`` from the local DB or forwards it
to the brain's ``/ui/api/*`` with the keychain token. The token never reaches
the browser. It also refuses to bind anything but loopback: the API is the
whole brain, and it has no auth of its own.
"""

from __future__ import annotations

import threading

import anyio
import httpx2
import pytest

from gingugu import remote, webui
from tests.proxy_fixtures import Served, free_port, make_brain
from tests.ui_api_fixtures import seed


class UiServer:
    def __init__(self) -> None:
        self.port = free_port()
        self.httpd = webui._build_server("127.0.0.1", self.port, serve_static=False, dist=None)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path: str) -> httpx2.Response:
        return httpx2.get(f"http://127.0.0.1:{self.port}{path}", timeout=10)


# --- bind ----------------------------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::"])
def test_refuses_a_non_loopback_bind(monkeypatch, host, capsys):
    called = []
    monkeypatch.setattr(webui, "serve_prod", lambda *a: called.append(a))
    assert webui.main(["--host", host, "--no-browser"]) == 2
    assert called == []
    assert "loopback" in capsys.readouterr().err


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_binds_are_fine(monkeypatch, host):
    called = []
    monkeypatch.setattr(webui, "serve_prod", lambda *a: called.append(a))
    assert webui.main(["--host", host, "--no-browser"]) == 0
    assert called


# --- local ---------------------------------------------------------------------


def test_local_mode_answers_from_the_local_db(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    with UiServer() as ui:
        graph = ui.get("/api/graph")
        assert graph.status_code == 200 and len(graph.json()["nodes"]) == 5
        info = ui.get("/api/info").json()
        assert info["mode"] == "local"
        assert info["brain"] == "local"


# --- remote --------------------------------------------------------------------


@pytest.fixture
def brain(tmp_path, monkeypatch):
    b = make_brain(tmp_path, monkeypatch, credentials=False)
    b.seeded = seed(tmp_path, monkeypatch)
    return b


@pytest.fixture
async def served(brain):
    s = Served(free_port())
    await s.start(brain.app())
    try:
        yield s
    finally:
        await s.stop()


def _go_remote(monkeypatch, tmp_path, url: str, token: str | None) -> None:
    # The local DB this process would read is empty: an answer with the seeded
    # nodes can only have come from the brain.
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "elsewhere" / "local.db"))
    monkeypatch.setenv("MEMORY_REMOTE_URL", url)
    if token:
        remote._kr_set(url, token)


async def test_remote_mode_forwards_with_the_keychain_token(served, brain, tmp_path, monkeypatch):
    _go_remote(monkeypatch, tmp_path, served.url, brain.machine)
    with UiServer() as ui:
        graph = await anyio.to_thread.run_sync(ui.get, "/api/graph")
        info = await anyio.to_thread.run_sync(ui.get, "/api/info")
    assert graph.status_code == 200 and len(graph.json()["nodes"]) == 5
    assert info.json()["mode"] == "remote"
    assert info.json()["brain"] == served.url.split("://", 1)[1]
    ui_calls = [(p, a) for p, a in brain.seen if p.startswith("/ui/api/")]
    assert ("/ui/api/graph", f"Bearer {brain.machine}") in ui_calls
    for response in (graph, info):
        assert brain.machine not in response.text


async def test_remote_mode_forwards_query_strings(served, brain, tmp_path, monkeypatch):
    _go_remote(monkeypatch, tmp_path, served.url, brain.machine)
    with UiServer() as ui:
        stats = await anyio.to_thread.run_sync(ui.get, "/api/stats?ns=crow")
    assert stats.json()["stats"]["total_memories"] == 2


async def test_mode_is_decided_per_request(served, brain, tmp_path, monkeypatch):
    _go_remote(monkeypatch, tmp_path, served.url, brain.machine)
    with UiServer() as ui:
        first = await anyio.to_thread.run_sync(ui.get, "/api/info")
        monkeypatch.setenv("MEMORY_REMOTE_URL", "off")
        second = await anyio.to_thread.run_sync(ui.get, "/api/info")
    assert first.json()["mode"] == "remote"
    assert second.status_code == 503  # local now, and this machine has no local brain
    assert second.json()["mode"] == "local"


def test_remote_unreachable_is_502(tmp_path, monkeypatch):
    url = f"http://127.0.0.1:{free_port()}"
    _go_remote(monkeypatch, tmp_path, url, "t" * 40)
    with UiServer() as ui:
        response = ui.get("/api/graph")
    assert response.status_code == 502
    body = response.json()
    assert body["ok"] is False and body["mode"] == "remote"


def test_remote_without_a_token_says_how_to_fix_it(tmp_path, monkeypatch):
    _go_remote(monkeypatch, tmp_path, f"http://127.0.0.1:{free_port()}", None)
    with UiServer() as ui:
        response = ui.get("/api/graph")
    assert response.status_code == 502
    assert "gingugu remote login" in response.json()["error"]


# --- DNS rebinding -------------------------------------------------------------
# Loopback alone does not stop a web page: rebind evil.example to 127.0.0.1 and
# the browser will happily send it here. The Host header is what tells them apart.


@pytest.mark.parametrize("host", ["evil.example:5174", "192.168.1.20:5174", "attacker.test"])
def test_foreign_host_header_is_refused(tmp_path, monkeypatch, host):
    seed(tmp_path, monkeypatch)
    with UiServer() as ui:
        response = httpx2.get(
            f"http://127.0.0.1:{ui.port}/api/graph", headers={"Host": host}, timeout=10
        )
    assert response.status_code == 403
    assert "nodes" not in response.text


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "[::1]:{port}"])
def test_loopback_host_headers_are_served(tmp_path, monkeypatch, host):
    seed(tmp_path, monkeypatch)
    with UiServer() as ui:
        response = httpx2.get(
            f"http://127.0.0.1:{ui.port}/api/graph",
            headers={"Host": host.format(port=ui.port)},
            timeout=10,
        )
    assert response.status_code == 200
