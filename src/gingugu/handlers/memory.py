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
    _check_pin_budget,
    _coerce_metadata,
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
        metadata: str | dict | None = None,
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
        ``suggested_relations`` list of up to 3 not-already-linked memories worth
        EXAMINING for a relationship. Topical overlap is only how they were
        found; it is not itself a reason to link. Ask whether one of them is the
        memory this one *supersedes*, *contradicts*, was *caused_by*, or belongs
        under - and if the honest answer is "they are just both about the same
        area", link nothing. Search already surfaces topical neighbours, so a
        `related_to` edge that says only "these are similar" adds no retrieval
        signal and competes with the directional edges that do. Distinct from
        ``similar_memories``: those are merge candidates.

        Both hint lists are COMPACT: title plus a ~200-char ``summary``, never
        full bodies. They are enough to decide whether to merge, link, or move
        on; call ``memory_recall`` when a candidate warrants a closer look.

        The response may also carry ``contradicted_memories``: older memories
        whose state claim THIS memory just resolved. Recording "PR #10 merged"
        makes every memory still asserting "PR #10 open" knowably wrong, and
        now is when fixing it is cheapest. Each entry gives the stale memory's
        ``id``, ``title``, the ``ref`` at issue, what it ``asserts``, and both
        sides' evidence.

        Reconcile by correcting the stale claim — the claim is now genuinely
        false, so the text should change. That is the opposite of rewording
        prose to silence a hint while the claim stays wrong. Advisory only:
        nothing was mutated."""
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

            if namespace is not None and "," in namespace:
                # get_or_create would mint a junk namespace literally named
                # "a,b" — fail fast instead of storing into it.
                return _err(
                    f"memory_store takes a single namespace, got {namespace!r}; "
                    "comma-separated lists are only supported by memory_context, "
                    "memory_recall, and memory_search"
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
                metadata=_coerce_metadata(metadata),
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
            response = {
                "ok": True,
                "memory": _memory_summary(mem),
                "namespace": ns_name,
                "similar_memories": similar,
                "suggested_relations": relations,
            }
            contradicted = ctx.store.contradicted_memories(mem)
            if contradicted:
                response["contradicted_memories"] = contradicted
            return response
        except Exception as exc:  # never crash the MCP loop
            logger.exception("memory_store failed")
            return _err(f"memory_store failed: {exc}")

    @mcp.tool()
    def memory_update(
        memory_id: str,
        title: str | None = None,
        content: str | None = None,
        type: str | None = None,
        confidence: str | None = None,
        metadata: str | dict | None = None,
        tags: str | None = None,
        resolve_claims: str | None = None,
        relation_check: bool = True,
        pinned: bool | None = None,
    ) -> dict:
        """Update one or more fields of an existing memory. Use to correct outdated
        information, promote confidence after confirming an inference, retype a
        misfiled memory, or add/replace tags. Do not create a new memory when the
        right action is to update an existing one — find the id first with
        memory_recall.

        All fields are optional; only provided fields are changed. ``tags``
        (comma-separated) replaces the full tag set when provided — omit to leave tags
        unchanged. Pass ``metadata=""`` to clear metadata; omit to leave it unchanged.

        ``type`` retypes the memory (same values as memory_store). Retyping is the
        right fix when a memory was filed under the wrong kind — e.g. durable
        reference material saved as ``workflow`` picks up point-in-time review
        hints, because ``pattern``/``preference`` are the types exempt from them.
        Retyping does not re-embed: the vector derives from title + content only.

        ``resolve_claims`` reconciles a stale state claim WITHOUT EDITING THE
        PROSE — comma-separated refs (e.g. "gingugu#10"), or "all" for every
        open claim on this memory. Use it when the text is accurate history: a
        session log that said "PR #10 open" was correct on the day it was
        written, and rewriting it to stay current destroys the record. The
        memory body is left byte-identical; only the claim's resolution is
        recorded. Reach for ``content`` instead only when the memory asserts
        something that was never true.

        "all" means every OPEN claim, never an ``unverified`` one. An unverified
        ref is one the prose names without saying what became of it, so sweeping
        it under "all" would record that you checked something you did not. Name
        such a ref explicitly to resolve it — that path works and is the honest
        way to say "I looked, and it merged".

        When ``relation_check`` is True (default) and ``title`` or ``content`` was
        provided, the response includes a ``suggested_relations`` list of up to 3
        not-already-linked memories worth examining for a relationship - same
        semantics as ``memory_store``: overlap is how they were found, and only a
        directional fact (supersedes / contradicts / caused_by / parent_of /
        child_of) justifies an edge. Tag-only or confidence-only updates skip the
        check since the matching surface didn't change. Entries are compact
        (title + a ~200-char ``summary``), as in ``memory_store``.

        ``pinned`` marks a memory as ALWAYS loaded by memory_context for its
        namespace, ahead of and exempt from ranking, in addition to ``limit``.
        Reserve it for the few rules that would cause real damage if missed —
        the ones you would want in front of you before touching anything, not
        merely useful or frequently relevant material. Ranking already handles
        "relevant"; a pin is for "inviolable". Capped per namespace (currently
        20): pinning is a budget, so spending it on a merely-handy memory
        crowds out a rule that governs behaviour. Pass ``pinned=False`` to
        unpin. Pinning does not touch ``last_confirmed`` — it is a retrieval
        decision, not a claim that the content is still true."""
        try:
            conf = None
            if confidence is not None:
                try:
                    conf = Confidence(confidence)
                except ValueError:
                    return _err(f"invalid confidence {confidence!r}")
            mem_type = None
            if type is not None:
                try:
                    mem_type = MemoryType(type)
                except ValueError:
                    return _err(
                        f"invalid type {type!r}; expected one of "
                        f"{[t.value for t in MemoryType]}"
                    )
            if pinned:
                refused = _check_pin_budget(ctx, memory_id)
                if refused is not None:
                    return refused
            mem = ctx.store.update(
                memory_id,
                title=title,
                content=content,
                type=mem_type,
                confidence=conf,
                metadata=_coerce_metadata(metadata),
                pinned=pinned,
            )
            if mem is None:
                return _err(f"memory {memory_id!r} not found")
            if tags is not None:
                ctx.store.set_tags(memory_id, _split_csv(tags))
            mem.tags = ctx.store.get_tags(memory_id)
            response: dict = {"ok": True, "memory": _memory_summary(mem)}
            if resolve_claims is not None:
                response["resolved_claims"] = ctx.store.resolve_claims(
                    memory_id, _split_csv(resolve_claims)
                )
            if relation_check and (title is not None or content is not None):
                response["suggested_relations"] = _suggest_relations(
                    ctx,
                    memory_id=mem.id,
                    namespace_id=mem.namespace_id,
                    title=mem.title,
                    content=mem.content,
                )
            # Correcting a memory to say "merged" is exactly when the OTHER
            # memories still saying "open" are worth surfacing.
            if title is not None or content is not None:
                contradicted = ctx.store.contradicted_memories(mem)
                if contradicted:
                    response["contradicted_memories"] = contradicted
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
