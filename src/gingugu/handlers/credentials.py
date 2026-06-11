"""Credential vault tool handlers: store / get / list / delete."""

from __future__ import annotations

import json
import logging

from ..credentials import CredentialVault
from . import ServerContext
from .memory import _err

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
        """Create/update a service credential bundle. ``fields`` is a JSON object
        like {"api_token": {"value": "...", "is_secret": true}}. Secrets go to
        the OS keychain; is_secret defaults to true."""
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
        """Retrieve a bundle including secret values from the keychain.
        ``fields`` is an optional comma-separated list to limit the response."""
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
        """List services + non-secret fields and expiry status (no keychain access)."""
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
        """Delete a service (or a single field). ``confirm`` must be true."""
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
