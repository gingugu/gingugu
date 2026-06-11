"""Relationship/consolidation tool handlers: relate and consolidate."""

from __future__ import annotations

import logging

from .. import consolidation
from ..models import RelationType
from ..relations import RelationManager
from . import ServerContext
from .memory import _err

logger = logging.getLogger(__name__)

_VALID_STRATEGIES = ("merge", "summarize", "deduplicate")


def register(mcp, ctx: ServerContext) -> None:
    relations = RelationManager(ctx.conn)

    @mcp.tool()
    def memory_relate(
        source_id: str,
        target_id: str,
        relation_type: str,
    ) -> dict:
        """Link two memories. ``relation_type`` is one of: supersedes,
        related_to, caused_by, contradicts, parent_of, child_of."""
        try:
            try:
                rel = RelationType(relation_type)
            except ValueError:
                return _err(
                    f"invalid relation_type {relation_type!r}; expected one of "
                    f"{[r.value for r in RelationType]}"
                )
            result = relations.relate(source_id=source_id, target_id=target_id, relation_type=rel)
            return {"ok": True, "relation": result}
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            logger.exception("memory_relate failed")
            return _err(f"memory_relate failed: {exc}")

    @mcp.tool()
    def memory_consolidate(
        memory_ids: str,
        strategy: str = "merge",
        keep_originals: bool = True,
    ) -> dict:
        """Consolidate memories. ``memory_ids`` is comma-separated. ``strategy``
        is one of: merge, summarize, deduplicate."""
        try:
            ids = [m.strip() for m in memory_ids.split(",") if m.strip()]
            if len(ids) < 2:
                return _err("memory_ids must list at least 2 ids")
            if strategy not in _VALID_STRATEGIES:
                return _err(f"invalid strategy {strategy!r}; expected one of {_VALID_STRATEGIES}")
            result = consolidation.consolidate(
                ctx.store,
                relations,
                memory_ids=ids,
                strategy=strategy,
                keep_originals=keep_originals,
            )
            return {"ok": True, **result}
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            logger.exception("memory_consolidate failed")
            return _err(f"memory_consolidate failed: {exc}")
