"""Admin tool handlers: namespace management and export/import.

All handlers wrap their work in try/except and return structured dict responses
so the MCP server never crashes the client flow.

A cascading namespace delete and an overwriting import ask the user, never the
model (see ``confirm``). No ``from __future__ import annotations``: the SDK
evaluates tool annotations against module globals, and ``Resolve(...)`` names
closures.
"""

import logging
from typing import Annotated

from mcp.server.elicitation import ElicitationResult
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.resolve import Resolve

from .. import confirm, grants, portability
from ..namespaces import DEFAULT_NAMESPACE
from . import ServerContext
from .helpers import _err, _single_namespace_not_found

logger = logging.getLogger(__name__)

_NS_ACTIONS = {"list", "create", "update", "delete"}


def _namespace_summary(ns) -> dict:
    return {
        "name": ns.name,
        "path": ns.path,
        "description": ns.description,
        "default_repo": ns.default_repo,
        "created_at": ns.created_at,
        "updated_at": ns.updated_at,
    }


def _overwrites(conn, data) -> int:
    """How many memories an ``on_conflict="replace"`` import would overwrite."""
    memories = data.get("memories") if isinstance(data, dict) else None
    if not isinstance(memories, list):
        return 0
    # Every id the import would DELETE by: SQLite matches by value, so no type filter.
    ids = list({m["id"] for m in memories if isinstance(m, dict) and m.get("id") is not None})
    total = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        sql = f"SELECT COUNT(*) FROM memories WHERE id IN ({marks})"  # noqa: S608 - placeholders
        total += conn.execute(sql, chunk).fetchone()[0]
    return total


def register(mcp, ctx: ServerContext) -> None:
    def _cascades(name: str | None) -> int:
        """Memories a cascading delete of ``name`` would take with it (0: none, or no such)."""
        if not name or name == DEFAULT_NAMESPACE:
            return 0
        ns = ctx.namespaces.get(name)
        return ctx.namespaces.count_memories(ns.id) if ns is not None else 0

    async def _ask_namespace(
        ctx_: Context, action: str = "list", name: str | None = None, cascade: bool = False
    ) -> object:
        if action != "delete" or not cascade:
            return confirm.NOT_NEEDED
        grant = confirm.fenced(ctx, "memory_namespaces", {"action": action})
        if grant is None:
            return confirm.FENCED
        with grants.bind(grant, ctx.conn):
            count = _cascades(name)
        if count == 0:
            return confirm.NOT_NEEDED
        return confirm.ask(
            ctx_,
            f"Delete namespace {confirm.shown(name)} and all {count} memories in it? "
            "This cannot be undone.",
            ctx.transport,
        )

    async def _ask_import(ctx_: Context, data: dict, on_conflict: str = "skip") -> object:
        if on_conflict != "replace":
            return confirm.NOT_NEEDED
        if confirm.fenced(ctx, "memory_import") is None:
            return confirm.FENCED
        count = _overwrites(ctx.conn, data)
        if count == 0:
            return confirm.NOT_NEEDED
        return confirm.ask(
            ctx_,
            f"Overwrite {count} existing memories with the imported copies? "
            "This cannot be undone.",
            ctx.transport,
        )

    @mcp.tool()
    def memory_namespaces(
        action: str = "list",
        name: str | None = None,
        path: str | None = None,
        description: str | None = None,
        default_repo: str | None = None,
        cascade: bool = False,
        answer: Annotated[ElicitationResult[confirm.Confirm], Resolve(_ask_namespace)] = None,
    ) -> dict:
        """Manage namespaces. ``action`` is one of: list, create, update, delete.

        - ``list`` — all namespaces with their memory counts.
        - ``create`` — create (or fetch) ``name`` with optional path/description.
        - ``update`` — update ``name``'s path/description/default_repo (only
          provided fields).
        - ``delete`` — remove ``name``; the ``default`` namespace is protected and
          a non-empty namespace requires ``cascade=True`` (deletes its memories),
          and the user confirms that through the client.

        ``default_repo`` controls what a bare "PR #12" in this namespace means.
        Leave it unset and the namespace's own name is used — the
        one-namespace-per-repo convention, and the right default. Pass a repo
        slug when the namespace is named differently from its repo. Pass ``""``
        to declare the namespace is **not** a repo (identity, notes, scratch),
        so bare refs are dropped rather than keyed to a repo that cannot exist.
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
                ns = ctx.namespaces.get_or_create(
                    name, path=path, description=description, default_repo=default_repo
                )
                return {"ok": True, "action": "created", "namespace": _namespace_summary(ns)}

            if action == "update":
                ns = ctx.namespaces.update(
                    name, path=path, description=description, default_repo=default_repo
                )
                if ns is None:
                    return _single_namespace_not_found(name)
                return {"ok": True, "action": "updated", "namespace": _namespace_summary(ns)}

            # action == "delete"
            if cascade and (count := _cascades(name)) > 0:
                refused = confirm.refusal(
                    answer, f"deleting namespace {name!r} and its {count} memories"
                )
                if refused is not None:
                    return _err(refused)
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
                    return _single_namespace_not_found(namespace)
                ns_id = ns.id
            payload = portability.export_data(
                ctx.conn, namespace_id=ns_id, include_deprecated=include_deprecated
            )
            return {"ok": True, "export": payload}
        except Exception as exc:
            logger.exception("memory_export failed")
            return _err(f"memory_export failed: {exc}")

    @mcp.tool()
    def memory_import(
        data: dict,
        on_conflict: str = "skip",
        answer: Annotated[ElicitationResult[confirm.Confirm], Resolve(_ask_import)] = None,
    ) -> dict:
        """Import a JSON payload produced by ``memory_export``.

        ``on_conflict`` is ``skip`` (default — leave existing memories untouched)
        or ``replace`` (overwrite existing memories with the same id; the user
        confirms through the client when anything would be overwritten). Namespaces
        are created if missing; tags and relations are restored.

        Imported memories are embedded as part of the import, so they are
        semantically searchable immediately rather than keyword-only. The
        summary reports ``embeddings_written``; on a large payload this is the
        slow part of the call.
        """
        try:
            if on_conflict not in ("skip", "replace"):
                return _err(f"invalid on_conflict {on_conflict!r}; expected 'skip' or 'replace'")
            if on_conflict == "replace" and (count := _overwrites(ctx.conn, data)) > 0:
                refused = confirm.refusal(answer, f"overwriting {count} memories by import")
                if refused is not None:
                    return _err(refused)
            result = portability.import_data(
                ctx.conn, data, on_conflict=on_conflict, embedder=ctx.store.embedder
            )
            return {"ok": True, **result}
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            logger.exception("memory_import failed")
            return _err(f"memory_import failed: {exc}")
