"""Admin tool handlers: namespace management and export/import.

All handlers wrap their work in try/except and return structured dict responses
so the MCP server never crashes the client flow.
"""

from __future__ import annotations

import logging

from .. import portability
from . import ServerContext
from .memory import _err

logger = logging.getLogger(__name__)

_NS_ACTIONS = {"list", "create", "update", "delete"}


def _namespace_summary(ns) -> dict:
    return {
        "name": ns.name,
        "path": ns.path,
        "description": ns.description,
        "created_at": ns.created_at,
        "updated_at": ns.updated_at,
    }


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool()
    def memory_namespaces(
        action: str = "list",
        name: str | None = None,
        path: str | None = None,
        description: str | None = None,
        cascade: bool = False,
    ) -> dict:
        """Manage namespaces. ``action`` is one of: list, create, update, delete.

        - ``list`` — all namespaces with their memory counts.
        - ``create`` — create (or fetch) ``name`` with optional path/description.
        - ``update`` — update ``name``'s path/description (only provided fields).
        - ``delete`` — remove ``name``; the ``default`` namespace is protected and
          a non-empty namespace requires ``cascade=True`` (deletes its memories).
        """
        try:
            if action not in _NS_ACTIONS:
                return _err(f"invalid action {action!r}; expected one of {sorted(_NS_ACTIONS)}")

            if action == "list":
                items = [
                    {**_namespace_summary(ns), "memory_count": ctx.namespaces.count_memories(ns.id)}
                    for ns in ctx.namespaces.list()
                ]
                return {"ok": True, "count": len(items), "namespaces": items}

            if not name:
                return _err(f"action {action!r} requires a 'name'")

            if action == "create":
                ns = ctx.namespaces.get_or_create(name, path=path, description=description)
                return {"ok": True, "action": "created", "namespace": _namespace_summary(ns)}

            if action == "update":
                ns = ctx.namespaces.update(name, path=path, description=description)
                if ns is None:
                    return _err(f"namespace {name!r} not found")
                return {"ok": True, "action": "updated", "namespace": _namespace_summary(ns)}

            # action == "delete"
            removed = ctx.namespaces.delete(name, cascade=cascade)
            return {"ok": True, "action": "deleted", "name": name, "memories_removed": removed}
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            logger.exception("memory_namespaces failed")
            return _err(f"memory_namespaces failed: {exc}")

    @mcp.tool()
    def memory_export(namespace: str | None = None, include_deprecated: bool = True) -> dict:
        """Export memories to a portable JSON payload (backup/transfer).

        Covers namespaces, memories, tags, and relations — **not** credentials
        (secrets live in the OS keychain). Scope to one ``namespace`` or export
        everything when omitted. Set ``include_deprecated=False`` to skip
        deprecated memories.
        """
        try:
            ns_id = None
            if namespace is not None:
                ns = ctx.namespaces.get(namespace)
                if ns is None:
                    return _err(f"namespace {namespace!r} not found")
                ns_id = ns.id
            payload = portability.export_data(
                ctx.conn, namespace_id=ns_id, include_deprecated=include_deprecated
            )
            return {"ok": True, "export": payload}
        except Exception as exc:
            logger.exception("memory_export failed")
            return _err(f"memory_export failed: {exc}")

    @mcp.tool()
    def memory_import(data: dict, on_conflict: str = "skip") -> dict:
        """Import a JSON payload produced by ``memory_export``.

        ``on_conflict`` is ``skip`` (default — leave existing memories untouched)
        or ``replace`` (overwrite existing memories with the same id). Namespaces
        are created if missing; tags and relations are restored.
        """
        try:
            if on_conflict not in ("skip", "replace"):
                return _err(f"invalid on_conflict {on_conflict!r}; expected 'skip' or 'replace'")
            result = portability.import_data(ctx.conn, data, on_conflict=on_conflict)
            return {"ok": True, **result}
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            logger.exception("memory_import failed")
            return _err(f"memory_import failed: {exc}")
