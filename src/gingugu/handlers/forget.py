"""The one destructive memory tool: ``memory_forget``.

Split from ``memory.py`` to keep that module under the 300-line limit. The seam
is the one worth having in this corner of the surface: ``memory_store`` and
``memory_update`` add and correct, while everything here can take something
away permanently. Nothing else in the codebase deletes a memory.
"""

from __future__ import annotations

import logging

from ..models import Confidence
from . import ServerContext
from .helpers import _err

logger = logging.getLogger(__name__)


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool()
    def memory_forget(
        memory_id: str,
        hard_delete: bool = False,
        reason: str | None = None,
    ) -> dict:
        """Mark a memory as no longer valid or permanently remove it. Default behavior
        (hard_delete=False) sets confidence to "deprecated", keeping the memory as a
        historical record but excluding it from future search results by default. Use
        hard_delete=True only when the memory must be permanently erased (e.g. sensitive
        data stored by mistake). Prefer deprecation over deletion when in doubt.

        ``reason`` is optional but recommended for audit trail — recorded in logs."""
        try:
            if hard_delete:
                deleted = ctx.store.delete(memory_id)
                if not deleted:
                    return _err(f"memory {memory_id!r} not found")
                return {"ok": True, "memory_id": memory_id, "action": "hard_deleted"}
            mem = ctx.store.update(memory_id, confidence=Confidence.DEPRECATED)
            if mem is None:
                return _err(f"memory {memory_id!r} not found")
            logger.info("Deprecated memory %s (reason=%s)", memory_id, reason)
            return {"ok": True, "memory_id": memory_id, "action": "deprecated"}
        except Exception as exc:
            logger.exception("memory_forget failed")
            return _err(f"memory_forget failed: {exc}")
