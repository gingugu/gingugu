"""Tests for keeping secret values out of the caller's context.

``credential_get`` used to return every secret in plaintext, so each use put
the value into the transcript - and into whatever the client's tooling logs -
before any discipline could apply. Now the default hides secret values, ``reveal``
is the explicit way to see one, and ``into`` hands a secret to a 0600 file so
the value never crosses the tool boundary at all.
"""

from __future__ import annotations

import json
import os
import stat
import sys

import keyring
import pytest

from gingugu import credentials as credentials_mod
from gingugu.credentials import CredentialVault

_POSIX = sys.platform != "win32"


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def _seed(vault: CredentialVault) -> None:
    vault.store(
        service_name="jira",
        fields={
            "base_url": {"value": "https://x.atlassian.net", "is_secret": False},
            "api_token": {"value": "sk-secret-123"},
        },
    )


# --- vault layer -------------------------------------------------------------


def test_get_redacts_secret_values_by_default(vault: CredentialVault) -> None:
    _seed(vault)
    bundle = vault.get("jira")
    token = bundle["fields"]["api_token"]
    assert token == {"is_secret": True, "redacted": True}
    assert "sk-secret-123" not in json.dumps(bundle)
    # Non-secret values are not what the vault protects, so they still resolve.
    assert bundle["fields"]["base_url"]["value"] == "https://x.atlassian.net"


def test_redacted_get_never_touches_the_keychain(vault: CredentialVault, monkeypatch) -> None:
    _seed(vault)

    def _boom(*_args, **_kwargs):
        raise AssertionError("keychain read on a redacted get")

    monkeypatch.setattr(credentials_mod.keyring, "get_password", _boom)
    assert vault.get("jira")["fields"]["api_token"]["redacted"] is True


def test_get_reveal_returns_the_value(vault: CredentialVault) -> None:
    _seed(vault)
    bundle = vault.get("jira", reveal=True)
    assert bundle["fields"]["api_token"] == {"value": "sk-secret-123", "is_secret": True}


def test_write_secret_creates_a_0600_file_with_the_raw_value(
    vault: CredentialVault, tmp_path
) -> None:
    _seed(vault)
    target = tmp_path / "token"
    result = vault.write_secret("jira", "api_token", str(target))
    assert target.read_bytes() == b"sk-secret-123"  # raw, no trailing newline
    assert result == {
        "service_name": "jira",
        "field": "api_token",
        "written": str(target),
        "bytes": 13,
        "mode": "0600",
    }
    assert "sk-secret-123" not in json.dumps(result)
    if _POSIX:
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(not _POSIX, reason="POSIX permission bits")
def test_write_secret_tightens_an_existing_file(vault: CredentialVault, tmp_path) -> None:
    _seed(vault)
    target = tmp_path / "token"
    target.write_text("old contents that are longer than the secret")
    os.chmod(target, 0o644)
    vault.write_secret("jira", "api_token", str(target))
    assert target.read_bytes() == b"sk-secret-123"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_secret_expands_home(vault: CredentialVault, tmp_path, monkeypatch) -> None:
    _seed(vault)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    result = vault.write_secret("jira", "api_token", "~/token")
    assert result["written"] == str(tmp_path / "token")
    assert (tmp_path / "token").read_bytes() == b"sk-secret-123"


@pytest.mark.parametrize("bad", ["relative/token", "token", ""])
def test_write_secret_refuses_a_relative_path(vault: CredentialVault, bad: str) -> None:
    _seed(vault)
    with pytest.raises(ValueError, match="absolute"):
        vault.write_secret("jira", "api_token", bad)


def test_write_secret_refuses_a_missing_parent(vault: CredentialVault, tmp_path) -> None:
    _seed(vault)
    with pytest.raises(ValueError, match="parent"):
        vault.write_secret("jira", "api_token", str(tmp_path / "nope" / "token"))


def test_write_secret_refuses_a_directory(vault: CredentialVault, tmp_path) -> None:
    _seed(vault)
    with pytest.raises(ValueError, match="directory"):
        vault.write_secret("jira", "api_token", str(tmp_path))


@pytest.mark.skipif(not _POSIX, reason="symlinks need privileges on Windows")
def test_write_secret_refuses_a_symlink(vault: CredentialVault, tmp_path) -> None:
    _seed(vault)
    real = tmp_path / "elsewhere"
    real.write_text("untouched")
    link = tmp_path / "token"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        vault.write_secret("jira", "api_token", str(link))
    assert real.read_text() == "untouched"


def test_write_secret_refuses_a_non_secret_field(vault: CredentialVault, tmp_path) -> None:
    _seed(vault)
    with pytest.raises(ValueError, match="not a secret"):
        vault.write_secret("jira", "base_url", str(tmp_path / "url"))
    assert not (tmp_path / "url").exists()


def test_write_secret_unknown_service_or_field(vault: CredentialVault, tmp_path) -> None:
    _seed(vault)
    with pytest.raises(LookupError):
        vault.write_secret("nope", "api_token", str(tmp_path / "a"))
    with pytest.raises(LookupError):
        vault.write_secret("jira", "nope", str(tmp_path / "b"))
    assert list(tmp_path.iterdir()) == []


def test_write_secret_locked_keychain_writes_nothing(
    vault: CredentialVault, tmp_path, monkeypatch
) -> None:
    _seed(vault)

    def _boom(*_args, **_kwargs):
        raise keyring.errors.KeyringError("keychain is locked")

    monkeypatch.setattr(credentials_mod.keyring, "get_password", _boom)
    target = tmp_path / "token"
    with pytest.raises(RuntimeError, match="unavailable"):
        vault.write_secret("jira", "api_token", str(target))
    assert not target.exists()


def test_write_secret_missing_keychain_entry_writes_nothing(
    vault: CredentialVault, tmp_path
) -> None:
    _seed(vault)
    keyring.delete_password(credentials_mod.KEYRING_SERVICE, "jira/api_token")
    target = tmp_path / "token"
    with pytest.raises(RuntimeError, match="no value"):
        vault.write_secret("jira", "api_token", str(target))
    assert not target.exists()
