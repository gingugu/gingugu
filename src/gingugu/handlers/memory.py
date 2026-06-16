"""Memory tool handlers: store, recall, context, update, forget.

All handlers wrap their work in try/except and return structured dict
responses — the MCP server must never crash the client flow.
"""

from __future__ import annotations

import logging

from .. import context as context_mod
from .. import search as search_mod
from ..models import Confidence, MemoryType
from . import ServerContext
from .helpers import (
    _collect_related,
    _err,
    _find_similar,
    _memory_summary,
    _split_csv,
    _spread_activation,
)

logger = logging.getLogger(__name__)


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool()
    def memory_store(
        content: str,
        title: str,
        type: str,
        namespace: str | None = None,
        tags: str | None = None,
        confidence: str = "inferred",
        source: str | None = None,
        metadata: str | None = None,
        dedupe_check: bool = True,
    ) -> dict:
        """Store a new memory in the knowledge base. Use to capture anything worth
        remembering across sessions: decisions, bugs, patterns, architecture choices,
        preferences, facts, workflows, or context. Do not use for ephemeral or
        session-only notes.

        ``type`` must be one of: fact, decision, pattern, bug, architecture, preference,
        workflow, context. ``confidence`` is one of: verified (confirmed true), inferred
        (assumed, not yet confirmed), stale (outdated), deprecated (no longer valid) —
        defaults to "inferred". ``tags`` is comma-separated. ``namespace`` scopes the
        memory to a project or domain; omit to use the configured default namespace.
        ``source`` records what generated this memory (e.g. a file path or tool name).
        ``metadata`` is an optional free-form JSON string for extra structured data.

        When ``dedupe_check`` is True (default), the response includes a
        ``similar_memories`` list of up to 3 existing memories in the same
        namespace whose content/title overlap strongly with this one — a
        non-blocking hint so the caller can choose to update/relate/consolidate
        instead of accumulating near-duplicates. Disable for bulk imports."""
        try:
            try:
                mem_type = MemoryType(type)
            except ValueError:
                return _err(
                    f"invalid type {type!r}; expected one of " f"{[t.value for t in MemoryType]}"
                )
            try:
                conf = Confidence(confidence)
            except ValueError:
                return _err(
                    f"invalid confidence {confidence!r}; expected one of "
                    f"{[c.value for c in Confidence]}"
                )

            ns_name = ctx.namespaces.resolve_name(namespace)
            ns = ctx.namespaces.get_or_create(ns_name)
            similar = (
                _find_similar(ctx, namespace_id=ns.id, title=title, content=content)
                if dedupe_check
                else []
            )
            mem = ctx.store.create(
                namespace_id=ns.id,
                type=mem_type,
                title=title,
                content=content,
                confidence=conf,
                source=source,
                metadata=metadata,
                tags=_split_csv(tags),
            )
            return {
                "ok": True,
                "memory": _memory_summary(mem),
                "namespace": ns_name,
                "similar_memories": similar,
            }
        except Exception as exc:  # never crash the MCP loop
            logger.exception("memory_store failed")
            return _err(f"memory_store failed: {exc}")

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
    ) -> dict:
        """Load the most relevant memories for the current session. Call this at session
        start with a brief description of the current task to prime the agent with
        useful context. Combines relevance to the task hint with recency, confidence,
        and access frequency to select the top memories. Also triggers spreading
        activation to wake related dormant memories.

        ``task_hint`` is a short description of what you are working on (e.g. "fix auth
        bug", "design API schema") — omit to surface generally high-value memories.
        ``limit`` defaults to MEMORY_AUTO_CONTEXT_LIMIT (10)."""
        try:
            ns_name = ctx.namespaces.resolve_name(namespace)
            # Session start in a fresh workspace is the one read that should
            # bootstrap its namespace, so get_or_create is intentional here.
            ns = ctx.namespaces.get_or_create(ns_name)
            results = context_mod.build_context(
                ctx.conn,
                namespace_id=ns.id,
                task_hint=task_hint,
                limit=limit if limit is not None else ctx.config.auto_context_limit,
                weights=ctx.config.weights,
                decay_lambda=ctx.config.decay_lambda,
                embedder=ctx.store.embedder,
            )
            ctx.store.load_tags(results)
            seed_ids = [m.id for m in results]
            # Credit the surfaced seeds as a real access (bumps access_count,
            # refreshes last_accessed, writes access_log row).
            ctx.store.record_accesses(seed_ids)
            # Spreading activation: surfacing context wakes the related cluster.
            _spread_activation(ctx, seed_ids)
            return {
                "ok": True,
                "namespace": ns_name,
                "count": len(results),
                "memories": [_memory_summary(m) for m in results],
            }
        except Exception as exc:
            logger.exception("memory_context failed")
            return _err(f"memory_context failed: {exc}")

    @mcp.tool()
    def memory_update(
        memory_id: str,
        title: str | None = None,
        content: str | None = None,
        confidence: str | None = None,
        metadata: str | None = None,
        tags: str | None = None,
    ) -> dict:
        """Update one or more fields of an existing memory. Use to correct outdated
        information, promote confidence after confirming an inference, or add/replace
        tags. Do not create a new memory when the right action is to update an existing
        one — find the id first with memory_recall.

        All fields are optional; only provided fields are changed. ``tags``
        (comma-separated) replaces the full tag set when provided — omit to leave tags
        unchanged. Pass ``metadata=""`` to clear metadata; omit to leave it unchanged."""
        try:
            conf = None
            if confidence is not None:
                try:
                    conf = Confidence(confidence)
                except ValueError:
                    return _err(f"invalid confidence {confidence!r}")
            mem = ctx.store.update(
                memory_id,
                title=title,
                content=content,
                confidence=conf,
                metadata=metadata,
            )
            if mem is None:
                return _err(f"memory {memory_id!r} not found")
            if tags is not None:
                ctx.store.set_tags(memory_id, _split_csv(tags))
            mem.tags = ctx.store.get_tags(memory_id)
            return {"ok": True, "memory": _memory_summary(mem)}
        except Exception as exc:
            logger.exception("memory_update failed")
            return _err(f"memory_update failed: {exc}")

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
