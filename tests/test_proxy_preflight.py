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


def test_a_well_formed_grant_is_allowed(vault):
    # The brain enforces it through a derived token; preflight only checks form.
    assert proxy.preflight(TARGET, grant="tyrone=write,crow=read", credentials_enabled=False)


@pytest.mark.parametrize("bad", ["tyrone", "tyrone=admin", "*=write", "", "   "])
def test_a_malformed_or_full_grant_is_refused(vault, bad):
    # "*=write" is no fence at all; unset MEMORY_GRANT for full access, as on stdio.
    assert "MEMORY_GRANT" in _refusal(grant=bad)


def test_credentials_enabled_is_allowed_for_the_owner(vault):
    # The proxy serves the vault from this machine; nothing to refuse.
    assert proxy.preflight(TARGET, grant=None, credentials_enabled=True) == TOKEN


def test_credentials_enabled_with_a_grant_is_refused(vault):
    # A scoped client never gets the vault, here or on stdio (fence._CLOSED_TO_SCOPED).
    message = _refusal(grant="tyrone=write,crow=read", credentials_enabled=True)
    assert "MEMORY_CREDENTIALS_ENABLED=false" in message


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
    _refusal(grant="alpha")
    _refusal(grant="tyrone=write", credentials_enabled=True)


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
    monkeypatch.setenv("MEMORY_GRANT", "tyrone=write")
    code, err = _main_exit(capsys)
    assert code == 2
    assert "MEMORY_CREDENTIALS_ENABLED=false" in err
    assert TOKEN not in err
    assert not (no_local_server / "local.db").exists()


@pytest.mark.parametrize("enabled, expected", [("true", "local.db"), ("false", None)])
def test_the_vault_db_is_handed_to_the_proxy_only_when_credentials_are_on(
    no_local_server, vault, monkeypatch, capsys, enabled, expected
):
    monkeypatch.setenv("MEMORY_REMOTE_URL", URL)
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", enabled)
    monkeypatch.delenv("MEMORY_GRANT", raising=False)
    seen = {}

    def fake_serve(url, token, *, grant, home, vault_db):
        seen["vault_db"] = vault_db
        return 0

    monkeypatch.setattr(proxy, "serve_stdio", fake_serve)
    code, _ = _main_exit(capsys)
    assert code == 0
    got = seen["vault_db"]
    assert (got.name if got is not None else None) == expected
    # main only names the file; the proxy opens it.
    assert not (no_local_server / "local.db").exists()


def test_a_blank_grant_is_refused_not_widened_to_full(no_local_server, vault, monkeypatch, capsys):
    # Set-but-empty must never fall back to the whole brain - the stdio rule.
    monkeypatch.setenv("MEMORY_REMOTE_URL", URL)
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "false")
    monkeypatch.setenv("MEMORY_GRANT", "")
    monkeypatch.setattr(proxy, "serve_stdio", lambda *a, **k: pytest.fail("proxy started"))
    code, err = _main_exit(capsys)
    assert code == 2 and "MEMORY_GRANT" in err


@pytest.mark.parametrize(
    "env, grant, home",
    [
        ({}, None, None),
        ({"MEMORY_NAMESPACE": "gingugu"}, None, "gingugu"),
        (
            {"MEMORY_NAMESPACE": "tyrone", "MEMORY_GRANT": "tyrone=write,crow=read"},
            "tyrone=write,crow=read",
            "tyrone",
        ),
    ],
)
def test_remote_ok_hands_off_to_the_proxy_with_grant_and_home(
    no_local_server, vault, monkeypatch, capsys, env, grant, home
):
    monkeypatch.setenv("MEMORY_REMOTE_URL", URL)
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "false")
    for key in ("MEMORY_GRANT", "MEMORY_NAMESPACE", "MEMORY_NAMESPACE_PATH"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    seen = {}

    def fake_serve(url, token, *, grant, home, vault_db):
        seen["args"] = (url, token, grant, home)
        return 0

    monkeypatch.setattr(proxy, "serve_stdio", fake_serve)
    code, _ = _main_exit(capsys)
    assert code == 0
    assert seen["args"] == (URL, TOKEN, grant, home)


def test_lost_brain_exits_1(no_local_server, vault, monkeypatch, capsys):
    monkeypatch.setenv("MEMORY_REMOTE_URL", URL)
    monkeypatch.setenv("MEMORY_CREDENTIALS_ENABLED", "false")

    def lost(url, token, **_):
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
