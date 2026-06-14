"""Search/stats tool handlers: ``memory_search`` and ``memory_stats``."""

from __future__ import annotations

import logging

from .. import search as search_mod
from .. import stats as stats_mod
from ..models import Confidence, MemoryType
from . import ServerContext
from .memory import _err, _memory_summary

logger = logging.getLogger(__name__)

_VALID_SORTS = {"relevance", "created", "accessed", "decay_score"}


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool()
    def memory_search(
        query: str | None = None,
        namespace: str | None = None,
        type: str | None = None,
        tags: str | None = None,
        confidence: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        sort_by: str = "relevance",
        include_deprecated: bool = False,
        limit: int = 10,
    ) -> dict:
        """Advanced filtered search. All parameters optional; ``tags`` is
        comma-separated (all required); ``sort_by`` is one of relevance,
        created, accessed, decay_score. ``include_deprecated`` also returns
        deprecated memories (stale ones are always included)."""
        try:
            if sort_by not in _VALID_SORTS:
                return _err(f"invalid sort_by {sort_by!r}; expected one of {sorted(_VALID_SORTS)}")
            if type is not None:
                try:
                    MemoryType(type)
                except ValueError:
                    return _err(f"invalid type {type!r}")
            min_conf = None
            if confidence is not None:
                try:
                    min_conf = Confidence(confidence)
                except ValueError:
                    return _err(f"invalid confidence {confidence!r}")

            ns_id = None
            if namespace is not None:
                ns = ctx.namespaces.get(namespace)
                if ns is None:
                    return _err(f"namespace {namespace!r} not found")
                ns_id = ns.id

            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
            results = search_mod.advanced_search(
                ctx.conn,
                query=query,
                namespace_id=ns_id,
                type=type,
                min_confidence=min_conf,
                created_after=created_after,
                created_before=created_before,
                sort_by=sort_by,
                include_deprecated=include_deprecated,
                limit=limit,
                weights=ctx.config.weights,
                decay_lambda=ctx.config.decay_lambda,
                tags=tag_list,
                embedder=ctx.store.embedder,
            )
            ctx.store.load_tags(results)
            return {
                "ok": True,
                "count": len(results),
                "memories": [_memory_summary(m) for m in results],
            }
        except Exception as exc:
            logger.exception("memory_search failed")
            return _err(f"memory_search failed: {exc}")

    @mcp.tool()
    def memory_stats(namespace: str | None = None, flag_stale: bool = False) -> dict:
        """Health overview: counts, dormancy, and per-namespace breakdown.

        ``stats.dormant_count`` reports memories untouched for 90+ days —
        a *resting* signal, never a confidence change. Memory is never
        auto-forgotten; dormant memories wake on recall (directly or via
        spreading activation through related memories).

        ``flag_stale`` is deprecated and ignored — the old behaviour
        (auto-demoting aged memories to ``stale``) contradicted the
        never-forget model and has been removed. The parameter is retained
        only so existing callers don't error."""
        try:
            ns_id = None
            if namespace is not None:
                ns = ctx.namespaces.get(namespace)
                if ns is None:
                    return _err(f"namespace {namespace!r} not found")
                ns_id = ns.id
            data = stats_mod.compute_stats(ctx.conn, namespace_id=ns_id)
            return {"ok": True, "flagged_stale": 0, "stats": data}
        except Exception as exc:
            logger.exception("memory_stats failed")
            return _err(f"memory_stats failed: {exc}")
