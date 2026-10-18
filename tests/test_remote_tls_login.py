"""`gingugu remote login` settles TLS before it rotates anything.

It asks the forced command for the brain's cert, checks the live brain with the
exact trust clients will use, writes the pin, and only then mints - so a stale
cert, a cert that misses the host, or a full disk costs a retry, never a
machine locked out with a rotated token.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.remote_tls_fixtures
from __future__ import annotations

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


def test_login_pins_the_cert_and_upgrades_to_https(pem, vault, monkeypatch, capsys):
    monkeypatch.setattr(remote, "_run", _runner([], cert=(0, pem.strip())))
    assert _login(HTTP) == 0
    assert remote_tls.load_pin(HTTPS) is not None
    assert vault == {HTTPS: TOKEN}  # the token is filed under the URL clients will use
    out = capsys.readouterr()
    assert HTTPS in out.out and serve_tls.fingerprint(pem) in out.out
    assert TOKEN not in out.out + out.err


def test_login_asks_for_the_cert_before_rotating_the_token(pem, vault, monkeypatch):
    calls: list = []
    monkeypatch.setattr(remote, "_run", _runner(calls, cert=(0, pem.strip())))
    _login()
    assert [argv[-1] for argv in calls][0] == "cert"
    assert calls[1][-2:] == ["mint", "mbp-2"]
    assert calls[0][:-1] == calls[1][:-2]  # same hardened ssh options and key


def test_login_to_a_brain_without_tls_keeps_http(vault, monkeypatch):
    monkeypatch.setattr(remote, "_run", _runner([], cert=(0, "")))
    assert _login(HTTP) == 0
    assert vault == {HTTP: TOKEN}
    assert remote_tls.load_pin(HTTP) is None


def test_login_to_an_older_brain_that_refuses_the_verb_keeps_http(vault, monkeypatch):
    monkeypatch.setattr(remote, "_run", _runner([], cert=(2, "")))
    assert _login(HTTP) == 0
    assert vault == {HTTP: TOKEN}


def test_login_with_https_to_a_brought_cert_needs_no_pin(vault, monkeypatch):
    monkeypatch.setattr(remote, "_run", _runner([], cert=(0, "")))
    assert _login(HTTPS) == 0
    assert vault == {HTTPS: TOKEN} and remote_tls.load_pin(HTTPS) is None


def test_login_fails_closed_on_a_garbled_cert(vault, monkeypatch):
    calls: list = []
    monkeypatch.setattr(remote, "_run", _runner(calls, cert=(0, "-----BEGIN CERTIFICATE-----x")))
    assert _login() == 1
    assert vault == {} and remote_tls.load_pin(HTTPS) is None
    assert all(argv[-1] == "cert" for argv in calls)  # never rotated the token


def test_login_fails_when_ssh_itself_fails_on_the_cert_step(vault, monkeypatch):
    calls: list = []
    monkeypatch.setattr(remote, "_run", _runner(calls, cert=(255, "")))
    assert _login() == 1
    assert vault == {} and len(calls) == 1


def test_login_checks_the_live_brain_with_the_pin_before_minting(pem, vault, monkeypatch, probes):
    calls: list = []
    monkeypatch.setattr(remote, "_run", _runner(calls, cert=(0, pem.strip())))
    assert _login(HTTP) == 0
    assert probes == [(HTTPS, remote_tls.parse_cert(pem))]


def test_a_brain_that_fails_the_live_check_rotates_nothing(pem, vault, monkeypatch, probes, capsys):
    # A stale cert from a different environment, or one that does not name the host.
    calls: list = []
    probes.fail = "the brain's certificate does not name brain.local"
    monkeypatch.setattr(remote, "_run", _runner(calls, cert=(0, pem.strip())))
    assert _login(HTTP) == 1
    assert [argv[-1] for argv in calls] == ["cert"]  # never minted
    assert vault == {} and remote_tls.load_pin(HTTPS) is None
    err = capsys.readouterr().err
    assert "does not name brain.local" in err and "nothing was rotated" in err


def test_the_pin_is_on_disk_before_the_token_is_rotated(pem, vault, monkeypatch):
    seen: list = []
    run = _runner([], cert=(0, pem.strip()))

    def spy(argv, **kwargs):
        if argv[-2:] == ["mint", "mbp-2"]:
            seen.append(remote_tls.load_pin(HTTPS) is not None)
        return run(argv, **kwargs)

    monkeypatch.setattr(remote, "_run", spy)
    assert _login(HTTP) == 0
    assert seen == [True]


def test_https_login_without_a_cert_drops_a_stale_pin(pem, vault, monkeypatch, probes):
    # The brain moved to a cert of its own: default trust, checked live first.
    remote_tls.save_pin(HTTPS, pem)
    monkeypatch.setattr(remote, "_run", _runner([], cert=(0, "")))
    assert _login(HTTPS) == 0
    assert probes == [(HTTPS, None)]
    assert remote_tls.load_pin(HTTPS) is None and vault == {HTTPS: TOKEN}


def test_http_login_to_a_pinned_brain_offering_no_cert_is_refused(pem, vault, monkeypatch, capsys):
    calls: list = []
    remote_tls.save_pin(HTTPS, pem)
    monkeypatch.setattr(remote, "_run", _runner(calls, cert=(0, "")))
    assert _login(HTTP) == 1
    assert [argv[-1] for argv in calls] == ["cert"] and vault == {}
    assert HTTPS in capsys.readouterr().err


def test_plain_http_login_is_not_probed(vault, monkeypatch, probes):
    monkeypatch.setattr(remote, "_run", _runner([], cert=(0, "")))
    assert _login(HTTP) == 0
    assert probes == []


def test_login_upgrades_a_machine_setting_already_on_this_brain(pem, vault, monkeypatch):
    vault[HTTP] = TOKEN
    assert remote.main(["on", HTTP]) == 0
    monkeypatch.setattr(remote, "_run", _runner([], cert=(0, pem.strip())))
    assert _login(HTTP) == 0
    assert remote.active().url == HTTPS


def test_login_still_does_not_switch_a_local_machine(pem, vault, monkeypatch):
    monkeypatch.setattr(remote, "_run", _runner([], cert=(0, pem.strip())))
    _login(HTTP)
    assert remote.active() is None
