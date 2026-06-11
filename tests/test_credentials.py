"""Tests for the credential vault (CRUD + keyring + expiry)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import keyring

from gingugu import credentials as credentials_mod
from gingugu.credentials import KEYRING_SERVICE, CredentialVault, expiry_status


def _future(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def test_store_and_get_roundtrip(vault: CredentialVault) -> None:
    vault.store(
        service_name="jira",
        fields={
            "base_url": {"value": "https://x.atlassian.net", "is_secret": False},
            "api_token": {"value": "sk-secret-123"},
        },
        description="Jira cloud",
    )
    bundle = vault.get("jira")
    assert bundle is not None
    assert bundle["fields"]["base_url"]["value"] == "https://x.atlassian.net"
    assert bundle["fields"]["base_url"]["is_secret"] is False
    assert bundle["fields"]["api_token"]["value"] == "sk-secret-123"
    assert bundle["fields"]["api_token"]["is_secret"] is True


def test_secret_stored_in_keyring_not_sqlite(vault: CredentialVault) -> None:
    vault.store(service_name="aws", fields={"key": {"value": "topsecret"}})
    # plain_value column must be NULL for secret fields.
    row = vault.conn.execute(
        "SELECT plain_value FROM credential_fields WHERE field_name = 'key'"
    ).fetchone()
    assert row["plain_value"] is None
    assert keyring.get_password(KEYRING_SERVICE, "aws/key") == "topsecret"


def test_list_does_not_expose_secrets(vault: CredentialVault) -> None:
    vault.store(
        service_name="gh",
        fields={"user": {"value": "me", "is_secret": False}, "token": {"value": "s"}},
    )
    services = vault.list()
    gh = next(s for s in services if s["service_name"] == "gh")
    assert gh["non_secret_fields"] == {"user": "me"}
    assert gh["secret_field_names"] == ["token"]
    assert "token" not in gh["non_secret_fields"]


def test_update_upserts_fields(vault: CredentialVault) -> None:
    vault.store(service_name="svc", fields={"a": {"value": "1"}})
    vault.store(service_name="svc", fields={"b": {"value": "2"}})
    bundle = vault.get("svc")
    assert set(bundle["fields"].keys()) == {"a", "b"}


def test_delete_field_then_service(vault: CredentialVault) -> None:
    vault.store(service_name="svc", fields={"a": {"value": "1"}, "b": {"value": "2"}})
    assert vault.delete("svc", field_name="a") is True
    assert keyring.get_password(KEYRING_SERVICE, "svc/a") is None
    bundle = vault.get("svc")
    assert set(bundle["fields"].keys()) == {"b"}
    assert vault.delete("svc") is True
    assert vault.get("svc") is None


def test_delete_missing_returns_false(vault: CredentialVault) -> None:
    assert vault.delete("nope") is False


def test_expiry_status() -> None:
    assert expiry_status(None) == "active"
    assert expiry_status(_future(60)) == "active"
    assert expiry_status(_future(5)) == "expiring_soon"
    assert expiry_status((datetime.now(UTC) - timedelta(days=1)).isoformat()) == "expired"


def test_health_summary(vault: CredentialVault) -> None:
    vault.store(service_name="a", fields={"k": {"value": "v"}}, expires_at=_future(60))
    vault.store(service_name="b", fields={"k": {"value": "v"}}, expires_at=_future(5))
    health = vault.health()
    assert health["total"] == 2
    assert health["expiring_soon"] == 1


def test_get_degrades_gracefully_when_keychain_unavailable(
    vault: CredentialVault, monkeypatch
) -> None:
    # Regression: a locked/unavailable keychain must not blow up the whole get;
    # it should return metadata + non-secret values, secret value=None + flag.
    vault.store(
        service_name="svc",
        fields={"url": {"value": "https://x", "is_secret": False}, "token": {"value": "s"}},
    )

    def _boom(*_args, **_kwargs):
        raise keyring.errors.KeyringError("keychain is locked")

    monkeypatch.setattr(credentials_mod.keyring, "get_password", _boom)

    bundle = vault.get("svc")
    assert bundle is not None
    # Non-secret field still resolves from SQLite.
    assert bundle["fields"]["url"]["value"] == "https://x"
    # Secret field degrades: no value, flagged unavailable, no exception raised.
    assert bundle["fields"]["token"]["value"] is None
    assert bundle["fields"]["token"]["unavailable"] is True
