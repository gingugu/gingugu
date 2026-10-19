"""The one destructive memory tool: ``memory_forget``.

Split from ``memory.py`` to keep that module under the 300-line limit. The seam
is the one worth having in this corner of the surface: ``memory_store`` and
``memory_update`` add and correct, while everything here can take something
away permanently. Nothing else in the codebase deletes a memory.

A hard delete asks the user, never the model (see ``confirm``).

No ``from __future__ import annotations``: the SDK evaluates the tool's
annotations against module globals, and ``Resolve(_ask)`` names a closure.
"""

import logging
from typing import Annotated

from mcp.server.elicitation import ElicitationResult
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.resolve import Resolve

from .. import confirm, grants
from ..models import Confidence
from . import ServerContext
from .helpers import _err

logger = logging.getLogger(__name__)


def register(mcp, ctx: ServerContext) -> None:
    async def _ask(memory_id: str, ctx_: Context, hard_delete: bool = False) -> object:
        if not hard_delete:
            return confirm.NOT_NEEDED
        grant = confirm.fenced(ctx, "memory_forget")
        if grant is None:
            return confirm.FENCED  # the wrapper refuses; never show a dialog for it
        with grants.bind(grant, ctx.conn):
            mem = ctx.store.get(memory_id, record_access=False)
        if mem is None:
            # The body answers "not found" before it reads the answer, so this
            # never lets a delete through: it only spares the person a dialog.
            return confirm.NOT_NEEDED
        return confirm.ask(
            ctx_, f'Permanently delete memory "{mem.title}" ({memory_id})? This cannot be undone.'
        )

    @mcp.tool()
    def memory_forget(
        memory_id: str,
        hard_delete: bool = False,
        reason: str | None = None,
        answer: Annotated[ElicitationResult[confirm.Confirm], Resolve(_ask)] = None,
    ) -> dict:
        """Mark a memory as no longer valid or permanently remove it. Default behavior
        (hard_delete=False) sets confidence to "deprecated", keeping the memory as a
        historical record but excluding it from future search results by default. Use
        hard_delete=True only when the memory must be permanently erased (e.g. sensitive
        data stored by mistake). Prefer deprecation over deletion when in doubt.

        A hard delete asks the user to confirm through the client; it is refused if
        they decline, or if the client cannot ask.

        ``reason`` is optional but recommended for audit trail — recorded in logs."""
        try:
            if hard_delete:
                if ctx.store.get(memory_id, record_access=False) is None:
                    return _err(f"memory {memory_id!r} not found")
                refused = confirm.refusal(answer, f"hard delete of {memory_id!r}")
                if refused is not None:
                    return _err(refused)
                deleted = ctx.store.delete(memory_id)
                if not deleted:
                    return _err(f"memory {memory_id!r} not found")
                logger.info("Hard-deleted memory %s (reason=%s)", memory_id, reason)
                return {"ok": True, "memory_id": memory_id, "action": "hard_deleted"}
            mem = ctx.store.update(memory_id, confidence=Confidence.DEPRECATED)
            if mem is None:
                return _err(f"memory {memory_id!r} not found")
            logger.info("Deprecated memory %s (reason=%s)", memory_id, reason)
            return {"ok": True, "memory_id": memory_id, "action": "deprecated"}
        except Exception as exc:
            logger.exception("memory_forget failed")
            return _err(f"memory_forget failed: {exc}")
