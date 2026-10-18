"""The SSH forced command's `cert` verb: the brain's public cert, and nothing else.

`gingugu remote login` asks for it before minting, over the channel it already
trusts. It must print only a certificate - never a private half, never a brought
cert - mint nothing, and refuse any other words.
"""

from __future__ import annotations

import pytest

from gingugu import remote_tls, serve_tls


@pytest.fixture
def mint_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memories.db"))
    for name in ("MEMORY_SERVE_TLS", "MEMORY_SERVE_TLS_CERT", "MEMORY_SERVE_TLS_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "cert")
    return tmp_path


def _ssh_mint(*argv: str) -> int:
    from gingugu.token_cli import main

    return main(["ssh-mint", *argv])


@pytest.mark.parametrize("bound", [(), ("--name", "mbp-2")])
def test_cert_verb_prints_the_generated_cert(mint_env, monkeypatch, capsys, bound):
    from gingugu.config import load_config

    monkeypatch.setenv("MEMORY_SERVE_HOST", "0.0.0.0")
    serve_tls.resolve(load_config())
    capsys.readouterr()
    assert _ssh_mint(*bound) == 0
    out = capsys.readouterr().out
    assert remote_tls.parse_cert(out) and "PRIVATE KEY" not in out


def test_cert_verb_never_prints_a_key_put_in_the_cert_file(mint_env, monkeypatch, capsys):
    from gingugu.config import load_config

    monkeypatch.setenv("MEMORY_SERVE_HOST", "0.0.0.0")
    files = serve_tls.resolve(load_config())
    key = files.key.read_text(encoding="ascii")
    files.cert.write_text(files.cert.read_text(encoding="ascii") + key, encoding="ascii")
    capsys.readouterr()
    assert _ssh_mint() == 1
    out = capsys.readouterr()
    assert "PRIVATE KEY" not in out.out + out.err


def test_cert_verb_with_no_cert_prints_nothing(mint_env, capsys):
    assert _ssh_mint() == 0
    assert capsys.readouterr().out == ""


def test_cert_verb_never_mints(mint_env, capsys):
    from gingugu.serve_tokens import TokenStore, default_path

    _ssh_mint("--name", "mbp-2")
    assert TokenStore(default_path()).list() == []


@pytest.mark.parametrize("original", ["cert extra", "cert mbp-2", "certs"])
def test_cert_verb_takes_no_arguments(mint_env, monkeypatch, capsys, original):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", original)
    assert _ssh_mint("--name", "mbp-2") == 2
    assert capsys.readouterr().out == ""
