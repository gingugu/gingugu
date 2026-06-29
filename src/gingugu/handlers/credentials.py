"""Credential vault tool handlers: store / get / list / delete."""

from __future__ import annotations

import json
import logging

from ..credentials import CredentialVault
from . import ServerContext
from .helpers import _err

logger = logging.getLogger(__name__)


def register(mcp, ctx: ServerContext) -> None:
    vault = CredentialVault(ctx.conn)

    @mcp.tool()
    def credential_store(
        service_name: str,
        fields: str,
        description: str | None = None,
        expires_at: str | None = None,
    ) -> dict:
        """Store or update a credential bundle (API keys, tokens, passwords) securely.
        Secret fields are written to the OS keychain; non-secret fields are stored in
        the database. Use instead of environment variables for credentials that need to
        be accessible to the agent across sessions.

        ``fields`` is a JSON object mapping field names to objects with "value" (required)
        and "is_secret" (optional, defaults true). ``expires_at`` is an optional ISO 8601
        datetime for expiry tracking. ``description`` is a human-readable label for the
        service. Example fields: {"api_token": {"value": "sk-...", "is_secret": true}}."""
        try:
            try:
                parsed = json.loads(fields)
            except json.JSONDecodeError as exc:
                return _err(f"fields must be valid JSON: {exc}")
            if not isinstance(parsed, dict) or not parsed:
                return _err("fields must be a non-empty JSON object")
            result = vault.store(
                service_name=service_name,
                fields=parsed,
                description=description,
                expires_at=expires_at,
            )
            return {"ok": True, **result}
        except Exception as exc:
            logger.exception("credential_store failed")
            return _err(f"credential_store failed: {exc}")

    @mcp.tool()
    def credential_get(service_name: str, fields: str | None = None) -> dict:
        """Retrieve all fields of a stored credential bundle, including secrets from the
        OS keychain. Use before making API calls that need stored credentials. Returns
        both secret and non-secret fields in one response. Returns an error if the
        service is not found — use credential_list first to discover available services.

        ``fields`` is an optional comma-separated list of field names to retrieve —
        omit to return all fields."""
        try:
            field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None
            bundle = vault.get(service_name, field_list)
            if bundle is None:
                return _err(f"service {service_name!r} not found")
            return {"ok": True, "service": bundle}
        except Exception as exc:
            logger.exception("credential_get failed")
            return _err(f"credential_get failed: {exc}")

    @mcp.tool()
    def credential_list(check_expiry: bool = True) -> dict:
        """List all stored credential services and their non-secret fields. Does NOT
        access the OS keychain — safe to call for discovery without triggering keychain
        prompts. Shows expiry status and flags expired or soon-to-expire credentials.
        Secret field values are never returned; use credential_get to retrieve secrets.

        ``check_expiry=False`` skips expiry calculation for faster results."""
        try:
            services = vault.list(check_expiry=check_expiry)
            return {"ok": True, "count": len(services), "services": services}
        except Exception as exc:
            logger.exception("credential_list failed")
            return _err(f"credential_list failed: {exc}")

    @mcp.tool()
    def credential_delete(
        service_name: str,
        confirm: bool,
        field_name: str | None = None,
    ) -> dict:
        """Permanently remove a credential service or a single field. Secret data is
        deleted from the OS keychain. This action is irreversible — there is no
        soft-delete for credentials. Use credential_list first to confirm what exists.

        ``confirm`` must be set to True to execute (prevents accidental deletion).
        ``field_name`` deletes only that field from the service; omit to delete the
        entire service and all its fields."""
        try:
            if not confirm:
                return _err("confirm must be true to delete")
            ok = vault.delete(service_name, field_name)
            if not ok:
                target = field_name or service_name
                return _err(f"nothing deleted; {target!r} not found")
            return {
                "ok": True,
                "deleted": field_name or service_name,
                "scope": "field" if field_name else "service",
            }
        except Exception as exc:
            logger.exception("credential_delete failed")
            return _err(f"credential_delete failed: {exc}")
