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
        """Advanced filtered search across memories with full control over filters and
        sort order. Use when you need to filter by type, date range, confidence level, or
        sort by something other than relevance. Prefer memory_recall when you just have a
        natural-language query and want the best-matching scored results.

        All parameters are optional — omitting all returns all memories up to limit.
        ``tags`` is comma-separated; all provided tags must match. ``sort_by`` is one of:
        relevance, created, accessed, decay_score. ``confidence`` sets a minimum
        confidence threshold (verified > inferred > stale > deprecated). ``created_after``
        and ``created_before`` accept ISO 8601 date strings (e.g. "2025-01-01").
        ``include_deprecated`` also returns deprecated memories (stale ones are always
        included)."""
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
            summaries = [_memory_summary(m) for m in results]
            # Credit the returned seeds as a real access (bumps access_count,
            # refreshes last_accessed, writes access_log row). Spreading-
            # activation neighbours are intentionally not credited here —
            # search has no relation traversal.
            ctx.store.record_accesses([m.id for m in results])
            return {
                "ok": True,
                "count": len(results),
                "memories": summaries,
            }
        except Exception as exc:
            logger.exception("memory_search failed")
            return _err(f"memory_search failed: {exc}")

    @mcp.tool()
    def memory_stats(namespace: str | None = None, flag_stale: bool = False) -> dict:
        """Return health statistics for the memory store. Use to monitor memory growth,
        identify dormant memories, and get a per-namespace breakdown of counts and
        confidence distribution. Call at session start alongside memory_context to assess
        the state of the knowledge base.

        ``stats.dormant_count`` reports memories untouched for 90+ days — a resting
        signal only, never a confidence change. Dormant memories wake automatically on
        recall via spreading activation. Memory is never auto-forgotten.

        ``flag_stale`` is deprecated and ignored — auto-demotion to stale contradicted
        the never-forget model and has been removed. Retained so existing callers do not
        error. ``namespace`` scopes the stats to a single namespace; omit for global."""
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
