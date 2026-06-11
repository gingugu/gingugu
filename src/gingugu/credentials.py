"""Credential vault: service bundles with OS-native secret storage.

Secret field values live in the OS keychain (via ``keyring``); SQLite stores
only metadata and non-secret values. Fully isolated from the memory system —
no decay, no FTS, no auto-context. See docs/architecture.md → Credential Vault.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import keyring

from .models import utcnow_iso

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "gingugu"
EXPIRING_SOON_DAYS = 14


def _keyring_account(service_name: str, field_name: str) -> str:
    return f"{service_name}/{field_name}"


def expiry_status(expires_at: str | None, now: datetime | None = None) -> str:
    """Classify a service as active / expiring_soon / expired."""
    if not expires_at:
        return "active"
    now = now or datetime.now(UTC)
    try:
        exp = datetime.fromisoformat(expires_at)
    except ValueError:
        return "active"
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if exp < now:
        return "expired"
    if exp <= now + timedelta(days=EXPIRING_SOON_DAYS):
        return "expiring_soon"
    return "active"


class CredentialVault:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def _get_service_row(self, service_name: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM credential_services WHERE service_name = ?", (service_name,)
        ).fetchone()

    def store(
        self,
        *,
        service_name: str,
        fields: dict[str, dict],
        description: str | None = None,
        expires_at: str | None = None,
    ) -> dict:
        """Create or update a service bundle (fields are upserted)."""
        now = utcnow_iso()
        row = self._get_service_row(service_name)
        if row is None:
            service_id = str(uuid.uuid4())
            self._conn.execute(
                "INSERT INTO credential_services"
                "(id, service_name, description, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (service_id, service_name, description, now, now, expires_at),
            )
        else:
            service_id = row["id"]
            self._conn.execute(
                "UPDATE credential_services SET description = COALESCE(?, description), "
                "expires_at = COALESCE(?, expires_at), updated_at = ? WHERE id = ?",
                (description, expires_at, now, service_id),
            )

        for field_name, spec in fields.items():
            is_secret = bool(spec.get("is_secret", True))
            value = spec.get("value")
            self._upsert_field(service_id, service_name, field_name, value, is_secret, now)

        self._conn.commit()
        return {"service_name": service_name, "fields": sorted(fields.keys())}

    def _upsert_field(
        self,
        service_id: str,
        service_name: str,
        field_name: str,
        value: str | None,
        is_secret: bool,
        now: str,
    ) -> None:
        plain = None if is_secret else value
        existing = self._conn.execute(
            "SELECT id FROM credential_fields WHERE service_id = ? AND field_name = ?",
            (service_id, field_name),
        ).fetchone()
        if existing is None:
            self._conn.execute(
                "INSERT INTO credential_fields"
                "(id, service_id, field_name, is_secret, plain_value, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), service_id, field_name, int(is_secret), plain, now, now),
            )
        else:
            self._conn.execute(
                "UPDATE credential_fields SET is_secret = ?, plain_value = ?, updated_at = ? "
                "WHERE id = ?",
                (int(is_secret), plain, now, existing["id"]),
            )
        if is_secret and value is not None:
            keyring.set_password(KEYRING_SERVICE, _keyring_account(service_name, field_name), value)

    def get(self, service_name: str, fields: list[str] | None = None) -> dict | None:
        """Return a full bundle including secret values pulled from the keychain."""
        row = self._get_service_row(service_name)
        if row is None:
            return None
        field_rows = self._conn.execute(
            "SELECT field_name, is_secret, plain_value FROM credential_fields WHERE service_id = ?",
            (row["id"],),
        ).fetchall()
        out_fields: dict[str, dict] = {}
        for fr in field_rows:
            name = fr["field_name"]
            if fields is not None and name not in fields:
                continue
            if fr["is_secret"]:
                value, available = self._safe_keyring_get(service_name, name)
                field = {"value": value, "is_secret": True}
                if not available:
                    # Keychain locked/unavailable: degrade gracefully (metadata
                    # without the secret) rather than failing the whole request.
                    field["unavailable"] = True
                out_fields[name] = field
            else:
                out_fields[name] = {"value": fr["plain_value"], "is_secret": False}
        return {
            "service_name": service_name,
            "description": row["description"],
            "expires_at": row["expires_at"],
            "status": expiry_status(row["expires_at"]),
            "fields": out_fields,
        }

    def list(self, check_expiry: bool = True) -> list[dict]:
        """List services with non-secret fields only (does not touch keychain)."""
        services = self._conn.execute(
            "SELECT * FROM credential_services ORDER BY service_name"
        ).fetchall()
        out: list[dict] = []
        for svc in services:
            non_secret = {
                fr["field_name"]: fr["plain_value"]
                for fr in self._conn.execute(
                    "SELECT field_name, plain_value FROM credential_fields "
                    "WHERE service_id = ? AND is_secret = 0",
                    (svc["id"],),
                ).fetchall()
            }
            secret_names = [
                fr["field_name"]
                for fr in self._conn.execute(
                    "SELECT field_name FROM credential_fields "
                    "WHERE service_id = ? AND is_secret = 1",
                    (svc["id"],),
                ).fetchall()
            ]
            entry = {
                "service_name": svc["service_name"],
                "description": svc["description"],
                "expires_at": svc["expires_at"],
                "non_secret_fields": non_secret,
                "secret_field_names": secret_names,
            }
            if check_expiry:
                entry["status"] = expiry_status(svc["expires_at"])
            out.append(entry)
        return out

    def delete(self, service_name: str, field_name: str | None = None) -> bool:
        """Delete a whole service or a single field, cleaning up keychain entries."""
        row = self._get_service_row(service_name)
        if row is None:
            return False
        if field_name is None:
            for fr in self._conn.execute(
                "SELECT field_name, is_secret FROM credential_fields WHERE service_id = ?",
                (row["id"],),
            ).fetchall():
                if fr["is_secret"]:
                    self._safe_keyring_delete(service_name, fr["field_name"])
            self._conn.execute("DELETE FROM credential_services WHERE id = ?", (row["id"],))
            self._conn.commit()
            return True

        field = self._conn.execute(
            "SELECT id, is_secret FROM credential_fields WHERE service_id = ? AND field_name = ?",
            (row["id"], field_name),
        ).fetchone()
        if field is None:
            return False
        if field["is_secret"]:
            self._safe_keyring_delete(service_name, field_name)
        self._conn.execute("DELETE FROM credential_fields WHERE id = ?", (field["id"],))
        self._conn.commit()
        return True

    @staticmethod
    def _safe_keyring_get(service_name: str, field_name: str) -> tuple[str | None, bool]:
        """Read a secret, degrading gracefully. Returns ``(value, available)``.

        On a keychain error (locked, backend missing) returns ``(None, False)``
        and logs a warning instead of raising, so a bundle still yields its
        metadata and non-secret fields. See docs/architecture.md risk register.
        """
        try:
            value = keyring.get_password(
                KEYRING_SERVICE, _keyring_account(service_name, field_name)
            )
            return value, True
        except keyring.errors.KeyringError as exc:
            logger.warning("Keychain unavailable for %s/%s: %s", service_name, field_name, exc)
            return None, False

    @staticmethod
    def _safe_keyring_delete(service_name: str, field_name: str) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, _keyring_account(service_name, field_name))
        except keyring.errors.PasswordDeleteError:
            logger.debug("keyring entry %s/%s already absent", service_name, field_name)

    def health(self) -> dict:
        """Summary for memory_stats: total, expired, expiring_soon."""
        rows = self._conn.execute("SELECT expires_at FROM credential_services").fetchall()
        statuses = [expiry_status(r["expires_at"]) for r in rows]
        return {
            "total": len(statuses),
            "expired": statuses.count("expired"),
            "expiring_soon": statuses.count("expiring_soon"),
        }
