"""Remote mode refuses to start rather than ever falling back to the local DB.

``proxy.preflight`` checks a remote target before any byte is relayed, and
``server.main`` routes a bare `gingugu` to the proxy - never ``build_server`` -
whenever a remote brain is active.
"""

from __future__ import annotations

import sys

import pytest

from gingugu import proxy, remote
from gingugu.remote import RemoteTarget

URL = "http://brain.local:8765"
TOKEN = "B" * 43
TARGET = RemoteTarget(URL, "env")


@pytest.fixture
def vault(monkeypatch):
    store: dict[str, str] = {URL: TOKEN}
    monkeypatch.setattr(remote, "_kr_get", lambda url: store.get(url))
    monkeypatch.setattr(remote, "_reachable", lambda url, token: True)
    return store


def _refusal(**overrides) -> str:
    kwargs = {"grant": None, "credentials_enabled": False, **overrides}
    with pytest.raises(proxy.ProxyRefused) as err:
        proxy.preflight(TARGET, **kwargs)
    message = str(err.value)
    assert TOKEN not in message
    return message


# --- preflight ---------------------------------------------------------------


def test_preflight_returns_the_keychain_token(vault):
    assert proxy.preflight(TARGET, grant=None, credentials_enabled=False) == TOKEN


def test_grant_is_refused_until_derived_tokens_exist(vault):
    assert "MEMORY_GRANT" in _refusal(grant="alpha=read")


def test_credentials_enabled_is_refused(vault):
    # The vault is this machine's keychain; a remote brain cannot serve it.
    assert "MEMORY_CREDENTIALS_ENABLED=false" in _refusal(credentials_enabled=True)


def test_missing_token_points_at_login(vault):
    vault.clear()
    assert "gingugu remote login" in _refusal()


def test_keychain_failure_is_a_refusal_not_a_crash(vault, monkeypatch):
    def broken(url):
        raise RuntimeError("locked")

    monkeypatch.setattr(remote, "_kr_get", broken)
    assert "keychain" in _refusal().lower()


def test_unreachable_brain_is_refused(vault, monkeypatch):
    monkeypatch.setattr(remote, "_reachable", lambda url, token: False)
    message = _refusal()
    assert "unreachable" in message.lower() and URL in message


def test_grant_is_checked_before_the_network(vault, monkeypatch):
    def boom(url, token):
        raise AssertionError("preflight touched the network for a refusable config")

    monkeypatch.setattr(remote, "_reachable", boom)
    _refusal(grant="alpha=read")
    _refusal(credentials_enabled=True)


# --- server.main dispatch ----------------------------------------------------


@pytest.fixture
def no_local_server(monkeypatch, tmp_path):
    """Fail the test if anything builds the local, DB-backed server."""
    monkeypatch.setattr(remote, "settings_path", lambda: tmp_path / "remote.json")
    monkeypatch.delenv("MEMORY_REMOTE_URL", raising=False)
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "local.db"))
    monkeypatch.setattr(sys, "argv", ["gingugu"])

    def forbidden(*a, **k):
        raise AssertionError("remote mode must never build the local server")

    monkeypatch.setattr("gingugu.server.build_server", forbidden)
    return tmp_path


def _main_exit(capsys) -> tuple[int, str]:
    from gingugu.server import main

    with pytest.raises(SystemExit) as exc:
        main()
    return exc.value.code, capsys.readouterr().err


def test_remote_refusal_exits_2_without_touching_the_local_db(
    no_local_server, vault, monkeypatch, capsys
):
    monkeypatch.setenv("MEMORY_REMOTE_URL", URL)
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "true")
    code, err = _main_exit(capsys)
    assert code == 2
    assert "MEMORY_CREDENTIALS_ENABLED=false" in err
    assert TOKEN not in err
    assert not (no_local_server / "local.db").exists()


def test_remote_ok_hands_off_to_the_proxy(no_local_server, vault, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_REMOTE_URL", URL)
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "false")
    monkeypatch.delenv("MEMORY_GRANT", raising=False)
    seen = {}

    def fake_serve(url, token):
        seen["args"] = (url, token)
        return 0

    monkeypatch.setattr(proxy, "serve_stdio", fake_serve)
    code, _ = _main_exit(capsys)
    assert code == 0
    assert seen["args"] == (URL, TOKEN)


def test_lost_brain_exits_1(no_local_server, vault, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_REMOTE_URL", URL)
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "false")

    def lost(url, token):
        raise proxy.ProxyLost("remote brain connection lost: ConnectError")

    monkeypatch.setattr(proxy, "serve_stdio", lost)
    code, err = _main_exit(capsys)
    assert code == 1
    assert "connection lost" in err and TOKEN not in err


def test_corrupt_machine_setting_exits_2_never_local(no_local_server, capsys):
    (no_local_server / "remote.json").write_text("{not json")
    code, err = _main_exit(capsys)
    assert code == 2 and "remote off" in err


def test_remote_off_runs_the_local_server(no_local_server, monkeypatch):
    monkeypatch.setenv("MEMORY_REMOTE_URL", "off")
    built = {}

    class _Local:
        def run(self):
            built["ran"] = True

    monkeypatch.setattr("gingugu.server.build_server", lambda *a, **k: _Local())
    from gingugu.server import main

    main()
    assert built == {"ran": True}
