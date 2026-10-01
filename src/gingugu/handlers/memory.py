"""Memory mutation tool handlers: store and update.

The write side of the memory surface. Read handlers (recall, context) live in
``recall.py``; the destructive one (``memory_forget``) in ``forget.py``.

All handlers wrap their work in try/except and return structured dict
responses — the MCP server must never crash the client flow.
"""

from __future__ import annotations

import logging

from .. import capability
from ..models import Confidence, MemoryType, Provenance
from . import ServerContext
from .capability_view import summary_with_capability, write_error
from .choices import parse_choice, parse_clearable
from .helpers import _check_pin_budget, _coerce_metadata, _err, _split_csv
from .hints import find_similar, suggest_relations

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
        provenance: str | None = None,
        about: str | None = None,
        dedupe_check: bool = True,
        relation_check: bool = True,
    ) -> dict:
        """Store a new memory in the knowledge base. Use to capture anything worth
        remembering across sessions: decisions, bugs, patterns, architecture choices,
        preferences, facts, workflows, or context. Do not use for ephemeral or
        session-only notes.

        ``type`` must be one of: fact, decision, pattern, bug, architecture, preference,
        workflow, context, capability. ``confidence`` is one of: verified (confirmed true), inferred
        (assumed, not yet confirmed), stale (outdated), deprecated (no longer valid) —
        defaults to "inferred". ``tags`` is comma-separated. ``namespace`` scopes the
        memory to a project or domain; omit to use the configured default namespace.
        ``source`` records what generated this memory (e.g. a file path or tool name).
        ``metadata`` is an optional free-form JSON string for extra structured data.

        ``capability`` records that a thing EXISTS and how to run it, where a
        ``workflow`` says how to do it by hand. It requires
        ``metadata={"capability": {"run": "...", "path": "..."}}`` (``run``
        required; a relative ``path`` resolves against the namespace's repo
        path). Reads report whether ``path`` still ``exists``.

        ``provenance`` declares how you came to believe this: user-asserted (the
        user said so), measured (a command or test showed it), file-derived (read
        from code or docs), or self-concluded (your own inference or opinion).
        Declare it honestly - self-concluded is how a stored opinion arrives
        visibly contestable instead of reading as settled fact. Any other value
        is rejected. ``about`` says what this memory is FOR, in the user's words
        for the thing rather than the words of what was done ("claude code
        bootstrapping", not "init writes hook blocks"). It is searchable.

        When ``dedupe_check`` is True (default), the response includes a
        ``similar_memories`` list of up to 3 existing memories in the same
        namespace whose content/title overlap strongly with this one — a
        non-blocking hint so the caller can choose to update/relate/consolidate
        instead of accumulating near-duplicates. Disable for bulk imports.
        **Usually EMPTY, and that is the signal working.** Each hit carries
        ``similarity`` (0-1) and its ``basis``: ``cosine`` over embeddings, or
        ``lexical`` token overlap when they are unavailable. Unlike a search
        relevance it has magnitude and is comparable between calls - it says
        how close the two texts are, not how this candidate ranked.

        When ``relation_check`` is True (default), the response also includes a
        ``suggested_relations`` list of up to 3 not-already-linked memories worth
        EXAMINING for a relationship. Topical overlap is only how they were
        found; it is not itself a reason to link. Ask whether one of them is the
        memory this one *supersedes*, *contradicts*, was *caused_by*, or belongs
        under - and if the honest answer is "they are just both about the same
        area", link nothing. Search already surfaces topical neighbours, so a
        `related_to` edge that says only "these are similar" adds no retrieval
        signal and competes with the directional edges that do. Same gate as
        above, set softer; ``similar_memories`` are merge candidates instead.

        Both hint lists are COMPACT: title plus a ~200-char ``summary``, never
        full bodies. They are enough to decide whether to merge, link, or move
        on; call ``memory_recall`` when a candidate warrants a closer look.
        Both carry ``similarity`` + ``basis``, never a search ``score``: these
        lists are not a ranking of the corpus, they are a measurement against
        what you just wrote.

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
                mem_type = parse_choice(MemoryType, type, "type")
                conf = parse_choice(Confidence, confidence, "confidence")
                prov = parse_choice(Provenance, provenance, "provenance")
            except ValueError as e:
                return _err(str(e))

            if namespace is not None and "," in namespace:
                # get_or_create would mint a junk namespace literally named
                # "a,b" — fail fast instead of storing into it.
                return _err(
                    f"memory_store takes a single namespace, got {namespace!r}; "
                    "comma-separated lists are only supported by memory_context, "
                    "memory_recall, and memory_search"
                )
            coerced = _coerce_metadata(metadata)
            if cap_error := capability.check(mem_type.value, coerced):
                return _err(cap_error)
            ns_name = ctx.namespaces.resolve_name(namespace)
            ns = ctx.namespaces.get_or_create(ns_name)
            similar = (
                find_similar(ctx, namespace_id=ns.id, title=title, content=content)
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
                metadata=coerced,
                tags=_split_csv(tags),
                provenance=prov,
                about=about,
            )
            relations = (
                suggest_relations(
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
                "memory": summary_with_capability(ctx, mem),
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
        provenance: str | None = None,
        about: str | None = None,
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
        Retyping does not re-embed: the vector derives from title + about + content.
        Capability validation applies to the FINAL state, so retyping to or from
        ``capability`` must bring or clear the ``metadata.capability`` block.

        ``provenance`` and ``about`` declare how the claim was reached and what it
        is for (see memory_store); pass ``""`` to clear either. Neither touches
        ``last_confirmed``: declaring them is not re-checking the claim.

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
        (title + a ~200-char ``summary``) and carry ``similarity`` + ``basis``,
        as in ``memory_store``; an empty list means nothing was close enough to
        be worth your time.

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
            try:
                conf = parse_choice(Confidence, confidence, "confidence")
                mem_type = parse_choice(MemoryType, type, "type")
                prov = parse_clearable(Provenance, provenance, "provenance")
            except ValueError as e:
                return _err(str(e))
            coerced = _coerce_metadata(metadata)
            existing = ctx.store.get(memory_id, record_access=False)
            if existing and (cap_error := write_error(existing, mem_type, metadata, coerced)):
                return _err(cap_error)
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
                metadata=coerced,
                pinned=pinned,
                provenance=prov,
                about=about,
            )
            if mem is None:
                return _err(f"memory {memory_id!r} not found")
            if tags is not None:
                ctx.store.set_tags(memory_id, _split_csv(tags))
            mem.tags = ctx.store.get_tags(memory_id)
            response: dict = {"ok": True, "memory": summary_with_capability(ctx, mem)}
            if resolve_claims is not None:
                response["resolved_claims"] = ctx.store.resolve_claims(
                    memory_id, _split_csv(resolve_claims)
                )
            if relation_check and (title is not None or content is not None):
                response["suggested_relations"] = suggest_relations(
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
