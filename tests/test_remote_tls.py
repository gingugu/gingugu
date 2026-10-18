"""The client side of TLS on the brain: a cert pinned over SSH, no manual step.

`gingugu remote login` asks the brain's forced command for its self-signed cert
over the SSH channel the login already trusts, pins it next to the machine
setting, and stores the URL as https. Every remote client then verifies the
brain against that pin alone. Once a brain is pinned, plain http to it is
refused, so a stale http URL fails loudly instead of sending the token in the
clear.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.remote_tls_fixtures
from __future__ import annotations

import ssl
import sys

import pytest

from gingugu import remote, remote_tls, serve_tls
from tests.remote_tls_fixtures import (  # noqa: F401 - fixtures
    HTTP,
    HTTPS,
    TOKEN,
    _login,
    _runner,
    pem,
    probes,
    vault,
)

# --- the pin -------------------------------------------------------------------


def test_no_pin_by_default():
    assert remote_tls.load_pin(HTTPS) is None
    assert remote_tls.verify_for(HTTPS) is True


def test_a_pin_lives_beside_the_machine_setting_owner_only(pem):
    remote_tls.save_pin(HTTPS, pem)
    path = remote_tls.pin_path(HTTPS)
    assert path.parent.parent == remote.settings_path().parent
    assert remote_tls.load_pin(HTTPS) == pem
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600


def test_a_pin_is_per_host_and_port_not_per_scheme(pem):
    remote_tls.save_pin(HTTPS, pem)
    assert remote_tls.load_pin(HTTP) == pem
    assert remote_tls.load_pin("https://brain.local:9999") is None
    assert remote_tls.load_pin("https://other.local:8765") is None


def test_save_pin_returns_the_fingerprint(pem):
    assert remote_tls.save_pin(HTTPS, pem) == serve_tls.fingerprint(pem)


def test_verify_for_a_pinned_https_brain_trusts_only_the_pin(pem):
    remote_tls.save_pin(HTTPS, pem)
    ctx = remote_tls.verify_for(HTTPS)
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.cert_store_stats()["x509"] == 1  # not the system store as well


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not a cert",
        "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n",
    ],
)
def test_parse_cert_refuses_garbage(bad):
    with pytest.raises(ValueError):
        remote_tls.parse_cert(bad)


def test_parse_cert_refuses_two_certs(pem):
    with pytest.raises(ValueError):
        remote_tls.parse_cert(pem + pem)


def test_parse_cert_refuses_a_key_riding_along(pem, tmp_path):
    key = (tmp_path / "brain.key").read_text(encoding="ascii")
    with pytest.raises(ValueError):
        remote_tls.parse_cert(pem + key)


# --- downgrade refusal -----------------------------------------------------------


def test_plain_http_to_a_pinned_brain_is_refused_loudly(pem, monkeypatch):
    remote_tls.save_pin(HTTPS, pem)
    monkeypatch.setenv("MEMORY_REMOTE_URL", HTTP)
    with pytest.raises(ValueError, match=HTTPS):
        remote.active()


def test_https_to_a_pinned_brain_is_fine(pem, monkeypatch):
    remote_tls.save_pin(HTTPS, pem)
    monkeypatch.setenv("MEMORY_REMOTE_URL", HTTPS)
    assert remote.active().url == HTTPS


def test_on_refuses_plain_http_to_a_pinned_brain(pem, vault, capsys):
    remote_tls.save_pin(HTTPS, pem)
    vault[HTTP] = TOKEN
    assert remote.main(["on", HTTP]) == 1
    assert HTTPS in capsys.readouterr().err
    assert not remote.settings_path().exists()


def test_an_unpinned_http_brain_still_works(monkeypatch):
    # Nothing changes for a brain that never offered TLS.
    monkeypatch.setenv("MEMORY_REMOTE_URL", HTTP)
    assert remote.active().url == HTTP


@pytest.mark.parametrize("content", ["", "garbage\n"])
def test_a_corrupt_pin_fails_closed_never_widens_to_default_trust(pem, monkeypatch, content):
    remote_tls.save_pin(HTTPS, pem)
    remote_tls.pin_path(HTTPS).write_text(content, encoding="ascii")
    with pytest.raises(ValueError, match="remote login"):
        remote_tls.verify_for(HTTPS)
    monkeypatch.setenv("MEMORY_REMOTE_URL", HTTPS)
    with pytest.raises(ValueError, match="remote login"):
        remote.active()


def test_parse_cert_refuses_text_around_the_block(pem):
    for bad in ("Welcome to the Pi!\n" + pem, pem + "logout\n"):
        with pytest.raises(ValueError):
            remote_tls.parse_cert(bad)


def test_status_reaches_a_pinned_brain_with_its_pin(pem, vault, monkeypatch, capsys):
    seen: list = []
    remote_tls.save_pin(HTTPS, pem)
    vault[HTTPS] = TOKEN
    remote.main(["on", HTTPS])

    def urlopen(*args, **kwargs):
        seen.append(kwargs)
        raise OSError("not reached")

    monkeypatch.setattr(remote.urllib.request, "urlopen", urlopen)
    remote.main(["status"])
    assert isinstance(seen[0].get("context"), ssl.SSLContext)


# --- naming a TLS failure ------------------------------------------------------------


def _cert_error(code: int, message: str) -> ssl.SSLCertVerificationError:
    err = ssl.SSLCertVerificationError()
    err.verify_code, err.verify_message = code, message
    return err


def test_a_clock_failure_is_retried_and_says_check_the_clock():
    for code in (9, 10):  # not yet valid, expired
        err = _cert_error(code, "certificate is not yet valid")
        assert remote_tls.is_clock(err)
        assert "clock" in remote_tls.explain(HTTPS, err)


def test_a_wrong_cert_names_both_fixes():
    err = _cert_error(18, "self-signed certificate")
    assert not remote_tls.is_clock(err)
    message = remote_tls.explain(HTTPS, err)
    assert "gingugu remote login" in message and "<db-dir>/tls" in message


async def test_a_corrupt_pin_ends_the_token_trade_cleanly(pem):
    from gingugu.proxy_session import ProxyLost, derive

    remote_tls.save_pin(HTTPS, pem)
    remote_tls.pin_path(HTTPS).write_text("", encoding="ascii")
    with pytest.raises(ProxyLost, match="corrupt"):
        await derive(HTTPS, "t", grant=None, home=None, ttl=300, name=None)
