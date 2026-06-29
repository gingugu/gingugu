"""Relationship/consolidation tool handlers: relate and consolidate."""

from __future__ import annotations

import logging

from .. import consolidation
from ..models import RelationType
from ..relations import RelationManager
from . import ServerContext
from .helpers import _err

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
        """Create a directional link between two memories. Relations are used by
        spreading activation (recalling one memory wakes its related cluster) and are
        returned when include_related=True in memory_recall. Use to build a knowledge
        graph that surfaces connected context automatically.

        ``source_id`` is the memory making the claim about ``target_id``. ``relation_type``
        must be one of: supersedes (source replaces target), related_to (general
        connection), caused_by (source was caused by target), contradicts (conflicting
        claims), parent_of (source contains target), child_of (source belongs to target)."""
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
        """Combine multiple memories into one to reduce redundancy and knowledge bloat.
        Use when several related memories about the same topic have accumulated over time.
        Do not use on memories that are still actively distinct — prefer memory_relate
        to link them instead.

        ``memory_ids`` is comma-separated (minimum 2 ids required). ``strategy`` is one
        of: merge (concatenate all content into one memory), summarize (produce a
        condensed combined summary), deduplicate (keep the highest-confidence entry and
        deprecate the rest). ``keep_originals=True`` (default) preserves originals as
        deprecated; set False to hard-delete them."""
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
