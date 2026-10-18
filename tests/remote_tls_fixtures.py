"""Shared rig for the TLS client suites: a generated cert, a fake keychain and ssh."""

from __future__ import annotations

import subprocess

import pytest

from gingugu import remote, remote_tls, serve_tls

HTTP = "http://brain.local:8765"
HTTPS = "https://brain.local:8765"
TOKEN = "A" * 43


@pytest.fixture
def pem(tmp_path) -> str:
    cert, key = tmp_path / "brain.pem", tmp_path / "brain.key"
    serve_tls.generate(cert, key, ["brain.local", "localhost", "127.0.0.1"])
    return cert.read_text(encoding="ascii")


@pytest.fixture
def vault(monkeypatch):
    """An in-memory keychain; the machine setting is already sandboxed by conftest."""
    store: dict[str, str] = {}
    monkeypatch.setattr(remote, "_kr_get", lambda url: store.get(url))
    monkeypatch.setattr(remote, "_kr_set", lambda url, tok: store.__setitem__(url, tok))
    monkeypatch.setattr(remote, "_kr_delete", lambda url: store.pop(url, None))
    return store


@pytest.fixture(autouse=True)
def probes(monkeypatch):
    """Login's live TLS check, recorded instead of dialled. Set ``.fail`` to refuse."""

    class Probes(list):
        fail: str | None = None

    seen = Probes()

    def probe(url, pem):
        seen.append((url, pem))
        if seen.fail:
            raise ValueError(seen.fail)

    monkeypatch.setattr(remote_tls, "probe", probe)
    return seen


def _runner(calls: list, *, cert: tuple[int, str] = (0, ""), mint: tuple[int, str] = (0, TOKEN)):
    """A fake ssh: answers the `cert` verb and the `mint` verb separately."""

    def run(argv, **kwargs):
        calls.append(argv)
        code, out = cert if argv[-1] == "cert" else mint
        return subprocess.CompletedProcess(argv, code, stdout=out + "\n" * bool(out), stderr="e")

    return run


def _login(url: str = HTTP) -> int:
    return remote.main(["login", url, "--ssh", "pi@brain.local", "--name", "mbp-2", "--key", "/k"])
