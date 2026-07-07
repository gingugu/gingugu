"""Memory retrieval tool handlers: recall, context.

The read side of the memory surface. Both tools fetch existing memories and
fire spreading activation to wake the related cluster. ``memory_recall``
credits its seeds as a real access; ``memory_context`` is a protocol-driven
read and only refreshes the dormancy clock (see the handler docstring).
Mutating handlers live in ``memory.py``.

All handlers wrap their work in try/except and return structured dict
responses — the MCP server must never crash the client flow.
"""

from __future__ import annotations

import logging

from .. import context as context_mod
from .. import search as search_mod
from .. import staleness
from ..models import Confidence, Memory, MemoryType
from . import ServerContext
from .helpers import (
    _collect_related,
    _compact_summary,
    _err,
    _memory_summary,
    _split_csv,
    _spread_activation,
)

logger = logging.getLogger(__name__)


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool()
    def memory_recall(
        query: str,
        namespace: str | None = None,
        type: str | None = None,
        confidence: str | None = None,
        tags: str | None = None,
        limit: int = 10,
        include_deprecated: bool = False,
        include_related: bool = False,
    ) -> dict:
        """Search memories by relevance using hybrid BM25 + semantic ranking. Use for
        natural-language queries when you want the best-matching memories for a topic.
        Prefer over memory_search when you have a query string and want scored results.
        Use memory_search instead when you need date filters, type filters, or a
        specific sort order.

        ``tags`` is comma-separated; ALL provided tags must match. ``confidence`` sets
        a minimum confidence threshold (verified > inferred > stale > deprecated).
        ``include_deprecated`` also returns deprecated memories (stale ones are always
        included). ``include_related`` also surfaces memories directly linked to the top
        hits via spreading activation — useful for pulling in a related cluster."""
        try:
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

            ns_name = ctx.namespaces.resolve_name(namespace)
            ns = ctx.namespaces.get(ns_name)
            if ns is None:
                if namespace is not None:
                    # Explicit unknown namespace is a caller mistake — don't
                    # silently create a junk row on a read (matches memory_search).
                    return _err(f"namespace {namespace!r} not found")
                # Config-resolved namespace with nothing stored yet: empty result.
                return {"ok": True, "namespace": ns_name, "count": 0, "memories": []}
            results = search_mod.search(
                ctx.conn,
                query=query,
                namespace_id=ns.id,
                type=type,
                min_confidence=min_conf,
                include_deprecated=include_deprecated,
                limit=limit,
                weights=ctx.config.weights,
                decay_lambda=ctx.config.decay_lambda,
                tags=_split_csv(tags) or None,
                embedder=ctx.store.embedder,
            )
            ctx.store.load_tags(results)
            seed_ids = [m.id for m in results]
            summaries = [_memory_summary(m) for m in results]
            if include_related:
                summaries.extend(_collect_related(ctx, seed_ids))
            # Credit the returned seeds as a real access (bumps access_count,
            # refreshes last_accessed, writes access_log row).
            ctx.store.record_accesses(seed_ids)
            # Spreading activation: recalling these memories wakes their cluster.
            _spread_activation(ctx, seed_ids)
            return {
                "ok": True,
                "namespace": ns_name,
                "count": len(summaries),
                "memories": summaries,
            }
        except Exception as exc:
            logger.exception("memory_recall failed")
            return _err(f"memory_recall failed: {exc}")

    @mcp.tool()
    def memory_context(
        namespace: str | None = None,
        task_hint: str | None = None,
        limit: int | None = None,
        compact: bool = False,
    ) -> dict:
        """Load the most relevant memories for the current session. Call this at session
        start with a brief description of the current task to prime the agent with
        useful context. Combines relevance to the task hint with recency, confidence,
        and access frequency to select the top memories. Also triggers spreading
        activation to wake related dormant memories.

        ``namespace`` accepts a single name or a comma-separated list (e.g.
        "crow,my-project"): a multi-namespace call loads every namespace in one
        shot and de-duplicates memories that surface in more than one, and each
        memory is stamped with its source ``namespace``. ``limit`` applies per
        namespace and defaults to MEMORY_AUTO_CONTEXT_LIMIT (10). ``task_hint``
        is a short description of what you are working on (e.g. "fix auth bug")
        — omit to surface generally high-value memories. ``compact=True``
        returns title + a ~200-char ``summary`` instead of full content — pull
        the full body with memory_recall when a memory matters.

        Context loads refresh each surfaced memory's dormancy clock but do not
        count as real accesses: ``access_count`` is reserved for
        memory_recall/memory_search hits, so protocol-driven session-start
        loads don't inflate ranking signals.

        A surfaced memory may carry ``review_hints`` — advisory signals that
        its content describes point-in-time state (an open PR, a "waiting on"
        note, a passed expiry date) that hasn't been confirmed recently.
        Reconcile with memory_update / memory_forget if it's no longer true."""
        try:
            requested = _split_csv(namespace)
            ns_names = list(dict.fromkeys(requested)) or [ctx.namespaces.resolve_name(None)]
            eff_limit = limit if limit is not None else ctx.config.auto_context_limit

            # Load each namespace, de-duplicating across them: a memory that
            # surfaces in several loads (typically via the cross-namespace
            # pattern bucket) keeps its highest-scoring instance.
            best: dict[str, Memory] = {}
            loaded_total = 0
            for name in ns_names:
                # Session start in a fresh workspace is the one read that should
                # bootstrap its namespace, so get_or_create is intentional here.
                ns = ctx.namespaces.get_or_create(name)
                for mem in context_mod.build_context(
                    ctx.conn,
                    namespace_id=ns.id,
                    task_hint=task_hint,
                    limit=eff_limit,
                    weights=ctx.config.weights,
                    decay_lambda=ctx.config.decay_lambda,
                    embedder=ctx.store.embedder,
                ):
                    loaded_total += 1
                    current = best.get(mem.id)
                    if current is None or (mem.score or 0.0) > (current.score or 0.0):
                        best[mem.id] = mem

            results = sorted(best.values(), key=lambda m: m.score or 0.0, reverse=True)
            ctx.store.load_tags(results)
            seed_ids = [m.id for m in results]
            # A context load is a protocol-driven read, not a real access:
            # refresh the dormancy clock only (no access_count bump, no
            # access_log row) so session-start loads don't inflate ranking.
            ctx.store.touch_many(seed_ids)
            # Spreading activation: surfacing context wakes the related cluster.
            _spread_activation(ctx, seed_ids)

            ns_name_by_id = {n.id: n.name for n in ctx.namespaces.list()}
            summaries = []
            for mem in results:
                summary = _compact_summary(mem) if compact else _memory_summary(mem)
                summary["namespace"] = ns_name_by_id.get(mem.namespace_id, mem.namespace_id)
                # Advisory nudge for point-in-time content ("PR #12 open,
                # waiting on…") that hasn't been confirmed recently — the
                # reader reconciles it, never the server.
                hints = staleness.review_signals(
                    mem.content,
                    last_confirmed=mem.last_confirmed,
                    updated_at=mem.updated_at,
                    created_at=mem.created_at,
                )
                if hints:
                    summary["review_hints"] = hints
                summaries.append(summary)

            payload: dict = {"ok": True, "count": len(results), "memories": summaries}
            if len(ns_names) == 1:
                payload["namespace"] = ns_names[0]
            else:
                payload["namespaces"] = ns_names
                payload["duplicates_removed"] = loaded_total - len(results)
            return payload
        except Exception as exc:
            logger.exception("memory_context failed")
            return _err(f"memory_context failed: {exc}")
