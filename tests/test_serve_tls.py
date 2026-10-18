"""`gingugu serve` is secure by default off the box, and zero-setup on it.

Local mode never touches a cert. A loopback serve stays plain http. A serve
bound to anything else gets TLS on its own: a self-signed cert generated once
next to the DB and reused after, unless the owner brings their own
(MEMORY_SERVE_TLS_CERT + MEMORY_SERVE_TLS_KEY) or opts out (MEMORY_SERVE_TLS=off).
"""

from __future__ import annotations

import sys

import pytest
from cryptography import x509

from gingugu import serve_tls
from gingugu.config import load_config

_TLS_ENV = (
    "MEMORY_SERVE_TLS",
    "MEMORY_SERVE_TLS_CERT",
    "MEMORY_SERVE_TLS_KEY",
    "MEMORY_SERVE_TLS_SAN",
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memories.db"))
    monkeypatch.delenv("MEMORY_SERVE_HOST", raising=False)
    for name in _TLS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(serve_tls.socket, "gethostname", lambda: "brainbox")

    def set_(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    return set_


def _sans(path) -> tuple[set[str], set[str]]:
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return set(san.get_values_for_type(x509.DNSName)), {
        str(ip) for ip in san.get_values_for_type(x509.IPAddress)
    }


# --- when TLS is on ----------------------------------------------------------


def test_loopback_default_stays_plain_and_writes_nothing(env, tmp_path):
    assert serve_tls.resolve(load_config()) is None
    assert not (tmp_path / "tls").exists()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_every_loopback_bind_stays_plain(env, host):
    env(MEMORY_SERVE_HOST=host)
    assert serve_tls.resolve(load_config()) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.50", "brain.local", "::"])
def test_a_network_bind_gets_tls_by_default(env, tmp_path, host):
    env(MEMORY_SERVE_HOST=host)
    files = serve_tls.resolve(load_config())
    assert files is not None and files.generated
    assert files.cert.parent == tmp_path / "tls" and files.cert.exists() and files.key.exists()


def test_off_opts_a_network_bind_out(env):
    env(MEMORY_SERVE_HOST="0.0.0.0", MEMORY_SERVE_TLS="off")
    assert serve_tls.resolve(load_config()) is None


def test_on_forces_tls_on_loopback(env):
    env(MEMORY_SERVE_TLS="on")
    assert serve_tls.resolve(load_config()) is not None


@pytest.mark.parametrize("mode", ["yes", "true", "https", " "])
def test_an_unknown_mode_refuses_to_start(env, mode):
    env(MEMORY_SERVE_HOST="0.0.0.0", MEMORY_SERVE_TLS=mode)
    with pytest.raises(ValueError, match="MEMORY_SERVE_TLS"):
        serve_tls.resolve(load_config())


# --- the generated cert ------------------------------------------------------


def test_generated_cert_names_this_machine(env):
    env(MEMORY_SERVE_HOST="0.0.0.0")
    dns, ips = _sans(serve_tls.resolve(load_config()).cert)
    assert {"brainbox", "brainbox.local", "localhost"} <= dns
    assert {"127.0.0.1", "::1"} <= ips


def test_a_specific_bind_ip_is_named_too(env):
    env(MEMORY_SERVE_HOST="192.168.1.50")
    _, ips = _sans(serve_tls.resolve(load_config()).cert)
    assert "192.168.1.50" in ips


def test_extra_names_come_from_memory_serve_tls_san(env):
    env(MEMORY_SERVE_HOST="0.0.0.0", MEMORY_SERVE_TLS_SAN="brain.example, 10.0.0.5")
    dns, ips = _sans(serve_tls.resolve(load_config()).cert)
    assert "brain.example" in dns and "10.0.0.5" in ips


def test_generated_cert_is_a_leaf_that_cannot_sign(env):
    env(MEMORY_SERVE_HOST="0.0.0.0")
    cert = x509.load_pem_x509_certificate(serve_tls.resolve(load_config()).cert.read_bytes())
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False


def test_generated_key_is_owner_only(env):
    env(MEMORY_SERVE_HOST="0.0.0.0")
    files = serve_tls.resolve(load_config())
    if sys.platform != "win32":
        assert files.key.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_a_reused_key_is_narrowed_back_to_owner_only(env):
    # A key restored from a backup at 0644 is tightened, not served as-is.
    env(MEMORY_SERVE_HOST="0.0.0.0")
    files = serve_tls.resolve(load_config())
    files.key.chmod(0o644)
    serve_tls.resolve(load_config())
    assert files.key.stat().st_mode & 0o777 == 0o600


def test_the_cert_is_reused_across_restarts(env):
    env(MEMORY_SERVE_HOST="0.0.0.0")
    first = serve_tls.resolve(load_config()).cert.read_bytes()
    assert serve_tls.resolve(load_config()).cert.read_bytes() == first


def test_a_lost_key_regenerates_both(env):
    env(MEMORY_SERVE_HOST="0.0.0.0")
    files = serve_tls.resolve(load_config())
    first = files.cert.read_bytes()
    files.key.unlink()
    again = serve_tls.resolve(load_config())
    assert again.key.exists() and again.cert.read_bytes() != first


# --- bring your own ----------------------------------------------------------


def test_own_cert_and_key_are_used_as_given(env, tmp_path):
    cert, key = tmp_path / "mine.pem", tmp_path / "mine.key"
    serve_tls.generate(cert, key, ["brain.example"])
    env(MEMORY_SERVE_HOST="0.0.0.0", MEMORY_SERVE_TLS_CERT=str(cert), MEMORY_SERVE_TLS_KEY=str(key))
    files = serve_tls.resolve(load_config())
    assert (files.cert, files.key, files.generated) == (cert, key, False)
    assert not (tmp_path / "tls").exists()


def test_own_cert_turns_tls_on_even_on_loopback(env, tmp_path):
    cert, key = tmp_path / "mine.pem", tmp_path / "mine.key"
    serve_tls.generate(cert, key, ["localhost"])
    env(MEMORY_SERVE_TLS_CERT=str(cert), MEMORY_SERVE_TLS_KEY=str(key))
    assert serve_tls.resolve(load_config()) is not None


@pytest.mark.parametrize("given", ["MEMORY_SERVE_TLS_CERT", "MEMORY_SERVE_TLS_KEY"])
def test_half_a_pair_refuses_to_start(env, tmp_path, given):
    env(MEMORY_SERVE_HOST="0.0.0.0", **{given: str(tmp_path / "x.pem")})
    with pytest.raises(ValueError, match="MEMORY_SERVE_TLS_CERT and MEMORY_SERVE_TLS_KEY"):
        serve_tls.resolve(load_config())


def test_a_missing_own_file_refuses_to_start(env, tmp_path):
    env(
        MEMORY_SERVE_HOST="0.0.0.0",
        MEMORY_SERVE_TLS_CERT=str(tmp_path / "nope.pem"),
        MEMORY_SERVE_TLS_KEY=str(tmp_path / "nope.key"),
    )
    with pytest.raises(ValueError, match="nope.pem"):
        serve_tls.resolve(load_config())


# --- what the SSH `cert` verb hands a client to pin ----------------------------


def test_cert_pem_is_the_generated_cert(env):
    env(MEMORY_SERVE_HOST="0.0.0.0")
    files = serve_tls.resolve(load_config())
    pem = serve_tls.cert_pem(load_config())
    assert pem == files.cert.read_text(encoding="ascii")
    assert "PRIVATE KEY" not in pem


def test_cert_pem_ignores_the_bind_host(env):
    # The SSH forced command does not run with the service's environment.
    env(MEMORY_SERVE_HOST="0.0.0.0")
    serve_tls.resolve(load_config())
    env(MEMORY_SERVE_HOST="127.0.0.1")
    assert serve_tls.cert_pem(load_config()) is not None


def test_cert_pem_is_none_before_any_cert_exists(env, tmp_path):
    assert serve_tls.cert_pem(load_config()) is None
    assert not (tmp_path / "tls").exists()  # never generates


def test_cert_pem_is_none_when_tls_is_off(env):
    env(MEMORY_SERVE_HOST="0.0.0.0")
    serve_tls.resolve(load_config())
    env(MEMORY_SERVE_TLS="off")
    assert serve_tls.cert_pem(load_config()) is None


def test_cert_pem_never_pins_a_brought_cert(env, tmp_path):
    # A real cert renews every few months; pinning it would break every client.
    cert, key = tmp_path / "mine.pem", tmp_path / "mine.key"
    serve_tls.generate(cert, key, ["brain.example"])
    env(MEMORY_SERVE_TLS_CERT=str(cert), MEMORY_SERVE_TLS_KEY=str(key))
    assert serve_tls.cert_pem(load_config()) is None


def test_fingerprint_is_sha256_colon_hex(env):
    env(MEMORY_SERVE_HOST="0.0.0.0")
    serve_tls.resolve(load_config())
    fp = serve_tls.fingerprint(serve_tls.cert_pem(load_config()))
    assert len(fp.split(":")) == 32 and all(len(b) == 2 for b in fp.split(":"))


# --- `gingugu serve` wires it to uvicorn ---------------------------------------


@pytest.fixture
def ran(env, monkeypatch):
    import uvicorn

    from tests.test_embeddings import FakeEmbedder

    monkeypatch.setattr("gingugu.server.build_provider", lambda **_: FakeEmbedder())
    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    return calls


def test_serve_on_a_network_bind_runs_https(env, ran, capsys):
    from gingugu.serve import serve

    env(MEMORY_SERVE_HOST="0.0.0.0")
    serve()
    assert ran[0]["ssl_certfile"].endswith("serve_cert.pem")
    assert ran[0]["ssl_keyfile"].endswith("serve_key.pem")
    log = capsys.readouterr().err  # build() resets logging to stderr, past caplog
    assert "https://0.0.0.0" in log
    assert "MEMORY_SERVE_TLS_CERT" in log and "MEMORY_SERVE_TLS=off" in log  # how to fix a client
    assert "gingugu remote login" in log
    assert "PRIVATE KEY" not in log


def test_serve_on_loopback_runs_plain_http(env, ran):
    from gingugu.serve import serve

    serve()
    assert "ssl_certfile" not in ran[0] or ran[0]["ssl_certfile"] is None


def test_serve_with_tls_off_on_a_network_bind_warns(env, ran, capsys):
    from gingugu.serve import serve

    env(MEMORY_SERVE_HOST="0.0.0.0", MEMORY_SERVE_TLS="off")
    serve()
    assert "unencrypted" in capsys.readouterr().err


def test_is_loopback():
    assert all(map(serve_tls.is_loopback, ["127.0.0.1", "localhost", "::1", "127.9.9.9"]))
    assert not any(map(serve_tls.is_loopback, ["0.0.0.0", "::", "192.168.1.5", "brain.local"]))
