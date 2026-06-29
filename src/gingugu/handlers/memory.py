"""Memory mutation tool handlers: store, update, forget.

The write side of the memory surface. Read handlers (recall, context) live in
``recall.py``.

All handlers wrap their work in try/except and return structured dict
responses — the MCP server must never crash the client flow.
"""

from __future__ import annotations

import logging

from ..models import Confidence, MemoryType
from . import ServerContext
from .helpers import (
    _err,
    _find_similar,
    _memory_summary,
    _split_csv,
    _suggest_relations,
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
        relation_check: bool = True,
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
        instead of accumulating near-duplicates. Disable for bulk imports.

        When ``relation_check`` is True (default), the response also includes a
        ``suggested_relations`` list of up to 3 memories with moderate topical
        overlap that aren't already linked — a nudge to call ``memory_relate``
        and grow the knowledge graph. Distinct from ``similar_memories``: those
        are merge candidates, these are link candidates."""
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
            relations = (
                _suggest_relations(
                    ctx,
                    memory_id=mem.id,
                    namespace_id=ns.id,
                    title=title,
                    content=content,
                    exclude_ids={s["id"] for s in similar},
                )
                if relation_check
                else []
            )
            return {
                "ok": True,
                "memory": _memory_summary(mem),
                "namespace": ns_name,
                "similar_memories": similar,
                "suggested_relations": relations,
            }
        except Exception as exc:  # never crash the MCP loop
            logger.exception("memory_store failed")
            return _err(f"memory_store failed: {exc}")

    @mcp.tool()
    def memory_update(
        memory_id: str,
        title: str | None = None,
        content: str | None = None,
        confidence: str | None = None,
        metadata: str | None = None,
        tags: str | None = None,
        relation_check: bool = True,
    ) -> dict:
        """Update one or more fields of an existing memory. Use to correct outdated
        information, promote confidence after confirming an inference, or add/replace
        tags. Do not create a new memory when the right action is to update an existing
        one — find the id first with memory_recall.

        All fields are optional; only provided fields are changed. ``tags``
        (comma-separated) replaces the full tag set when provided — omit to leave tags
        unchanged. Pass ``metadata=""`` to clear metadata; omit to leave it unchanged.

        When ``relation_check`` is True (default) and ``title`` or ``content`` was
        provided, the response includes a ``suggested_relations`` list of up to 3
        existing memories worth linking that aren't already related. Tag-only or
        confidence-only updates skip the check since the matching surface didn't
        change."""
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
            response: dict = {"ok": True, "memory": _memory_summary(mem)}
            if relation_check and (title is not None or content is not None):
                response["suggested_relations"] = _suggest_relations(
                    ctx,
                    memory_id=mem.id,
                    namespace_id=mem.namespace_id,
                    title=mem.title,
                    content=mem.content,
                )
            return response
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
