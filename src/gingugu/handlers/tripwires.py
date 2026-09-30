"""The ``memory_tripwire`` tool: bind a memory to a tool call that should be stopped.

The matching and CRUD live in ``tripwire``; this is the MCP surface over them.
The PreToolUse hook (``gingugu hook tool``) is what actually denies a call.
"""

from __future__ import annotations

import dataclasses
import json
import logging

from .. import tripwire
from . import ServerContext
from .helpers import _err

logger = logging.getLogger(__name__)

_ACTIONS = ("add", "list", "remove", "test")


def _split(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool()
    def memory_tripwire(
        action: str,
        memory_id: str | None = None,
        tool: str | None = None,
        pattern: str | None = None,
        tripwire_id: str | None = None,
        input: str | dict | None = None,
        namespace: str | None = None,
    ) -> dict:
        """Bind a memory to a tool call so it is put in front of the agent at the moment
        it acts, not just at session start. Use it for the memories that must not be
        missed: "never sign commits as Claude", "never touch the prod config".

        Actions: "add" (memory_id, tool, pattern), "list" (optional memory_id,
        namespace as a comma-separated list), "remove" (tripwire_id), "test" (tool,
        input, optional namespace).

        ``tool`` is a regex FULL-matched against the tool name, e.g. "Bash" or
        "Edit|Write" ("Bash" does not catch "BashOutput"). ``pattern`` is a regex
        SEARCHED in the call's input: the command for Bash, the file_path for
        Edit/Write/Read, the notebook_path for NotebookEdit, otherwise the input as
        sorted JSON.

        The PreToolUse hook denies the first matching call per session with the
        memory as the reason; the re-issued call passes. Use "test" to check a pattern
        before relying on it: ``input`` is the tool input as a JSON object string, and
        nothing is denied or logged. gingugu's own mcp__gingugu__ tools never trip."""
        try:
            if action == "add":
                if not memory_id:
                    return _err("memory_id is required for action 'add'")
                if not tool or not pattern:
                    return _err("tool and pattern are required for action 'add'")
                try:
                    row = tripwire.add_tripwire(ctx.conn, memory_id, tool, pattern)
                except ValueError as exc:
                    return _err(str(exc))
                ctx.conn.commit()
                return {"ok": True, "tripwire": row}
            if action == "list":
                rows = tripwire.list_tripwires(
                    ctx.conn, memory_id=memory_id, namespaces=_split(namespace) or None
                )
                return {"ok": True, "count": len(rows), "tripwires": rows}
            if action == "remove":
                if not tripwire_id:
                    return _err("tripwire_id is required for action 'remove'")
                removed = tripwire.remove_tripwire(ctx.conn, tripwire_id)
                ctx.conn.commit()
                if not removed:
                    return _err(f"tripwire {tripwire_id!r} not found")
                return {"ok": True, "removed": tripwire_id}
            if action == "test":
                if not tool:
                    return _err("tool is required for action 'test'")
                try:
                    # FastMCP pre-parses a JSON-object string into a dict before
                    # the handler sees it, so accept both forms.
                    parsed = json.loads(input) if isinstance(input, str) and input else input
                    parsed = {} if parsed is None or parsed == "" else parsed
                except json.JSONDecodeError as exc:
                    return _err(f"input is not valid JSON: {exc}")
                if not isinstance(parsed, dict):
                    return _err("input must be a JSON object")
                names = _split(namespace)
                if not names:
                    names = [ctx.namespaces.resolve_name(), "crow"]
                names = list(dict.fromkeys(names))
                wires = tripwire.load_tripwires(ctx.conn, names)
                hits = tripwire.matching(wires, tool, parsed)
                return {"ok": True, "matches": [dataclasses.asdict(w) for w in hits]}
            return _err(f"unknown action {action!r}; valid actions: {', '.join(_ACTIONS)}")
        except Exception as exc:
            logger.exception("memory_tripwire failed")
            return _err(f"memory_tripwire failed: {exc}")
