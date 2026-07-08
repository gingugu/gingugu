"""Shared helpers for memory tool handlers."""

from __future__ import annotations

import json
import logging

from .. import search as search_mod
from .. import staleness
from ..models import Memory, Namespace
from ..relations import RelationManager
from . import ServerContext

logger = logging.getLogger(__name__)


def _err(message: str) -> dict:
    return {"ok": False, "error": message}


def _coerce_metadata(metadata: str | dict | list | None) -> str | None:
    """Accept metadata as a JSON string *or* an already-parsed object.

    Over HTTP transports the MCP layer hands a JSON-object argument to the
    server as a ``dict``, so a ``str``-only param would reject it. Serialize
    dict/list back to a JSON string for the storage layer (which validates
    and stores JSON text); pass strings/None through unchanged.
    """
    if isinstance(metadata, (dict, list)):
        return json.dumps(metadata)
    return metadata


def _split_csv(value: str | None) -> list[str]:
    # Drop empty items so "crow," / "a,,b" don't yield "" entries — an empty
    # string reaching get_or_create would mint a namespace literally named "".
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


def _resolve_namespaces(
    ctx: ServerContext, names: list[str]
) -> tuple[dict[str, Namespace], dict | None]:
    """Resolve explicit namespace names for a read surface.

    Returns ``(resolved, error)``: ``resolved`` maps each name to its
    ``Namespace`` in request order; ``error`` is a structured error dict naming
    every unknown namespace (reads must never mint namespaces — matches the
    single-namespace behavior of memory_recall/memory_search).
    """
    resolved: dict[str, Namespace] = {}
    missing: list[str] = []
    for name in names:
        ns = ctx.namespaces.get(name)
        if ns is None:
            missing.append(name)
        else:
            resolved[name] = ns
    if missing:
        if len(missing) == 1:
            return {}, _err(f"namespace {missing[0]!r} not found")
        listed = ", ".join(repr(n) for n in missing)
        return {}, _err(f"namespaces {listed} not found")
    return resolved, None


def _single_namespace_not_found(namespace: str) -> dict:
    """Unknown-namespace error for tools that take exactly one namespace.

    When the value contains a comma the caller almost certainly generalized the
    CSV form from the multi-namespace read tools — say so in the error instead
    of leaving them guessing.
    """
    msg = f"namespace {namespace!r} not found"
    if "," in namespace:
        msg += (
            " (this tool takes a single namespace; comma-separated lists are "
            "supported by memory_context, memory_recall, and memory_search)"
        )
    return _err(msg)


def _stamp_namespace_names(ctx: ServerContext, summaries: list[dict]) -> None:
    """Add a human-readable ``namespace`` field to each summary in place.

    Uses a global id→name map so related memories pulled in from *other*
    namespaces (via include_related) are stamped correctly too.
    """
    name_by_id = {n.id: n.name for n in ctx.namespaces.list()}
    for summary in summaries:
        ns_id = summary.get("namespace_id")
        summary["namespace"] = name_by_id.get(ns_id, ns_id)


def _memory_summary(mem: Memory) -> dict:
    data = {
        "id": mem.id,
        "type": mem.type.value,
        "title": mem.title,
        "content": mem.content,
        "confidence": mem.confidence.value,
        "namespace_id": mem.namespace_id,
        "created_at": mem.created_at,
        "last_confirmed": mem.last_confirmed,
        "access_count": mem.access_count,
        "tags": mem.tags,
    }
    if mem.score is not None:
        data["score"] = round(mem.score, 4)
    return data


# Compact summaries replace full content with a short excerpt. 200 chars is
# roughly two terminal lines — enough to recognize the memory and decide
# whether to pull the full body via memory_recall.
_COMPACT_CONTENT_CHARS = 200


def _compact_summary(mem: Memory) -> dict:
    """Lightweight variant of ``_memory_summary`` for ``compact`` reads
    (memory_context, memory_recall, memory_search).

    Full ``content`` is replaced by a whitespace-normalized excerpt under
    ``summary``; bookkeeping fields (timestamps, access_count) are dropped.
    ``namespace_id`` is identity, not bookkeeping — kept so namespace
    stamping works uniformly across full and compact payloads.
    """
    excerpt = " ".join(mem.content.split())
    if len(excerpt) > _COMPACT_CONTENT_CHARS:
        excerpt = excerpt[:_COMPACT_CONTENT_CHARS].rsplit(" ", 1)[0] + " …"
    data = {
        "id": mem.id,
        "type": mem.type.value,
        "title": mem.title,
        "summary": excerpt,
        "confidence": mem.confidence.value,
        "namespace_id": mem.namespace_id,
        "tags": mem.tags,
    }
    if mem.score is not None:
        data["score"] = round(mem.score, 4)
    return data


def _attach_review_hints(summary: dict, mem: Memory) -> dict:
    """Stamp advisory ``review_hints`` onto a summary when the memory's content
    describes point-in-time state that hasn't been confirmed recently.

    Shared by every read surface (context, recall, search) so hint absence
    means the same thing everywhere: no staleness signal detected.
    """
    hints = staleness.review_signals(
        mem.content,
        last_confirmed=mem.last_confirmed,
        updated_at=mem.updated_at,
        created_at=mem.created_at,
    )
    if hints:
        summary["review_hints"] = hints
    return summary


def _collect_related(ctx: ServerContext, seed_ids: list[str], compact: bool = False) -> list[dict]:
    """Fetch memories directly related to the seeds, excluding the seeds.

    ``compact`` mirrors the caller's payload mode — related extras are part of
    the same response and must not reinflate a compact read.
    """
    relations = RelationManager(ctx.conn)
    seen = set(seed_ids)
    extra: list[dict] = []
    for sid in seed_ids:
        for other_id in relations.related_ids(sid):
            if other_id in seen:
                continue
            seen.add(other_id)
            mem = ctx.store.get(other_id, record_access=False)
            if mem is None:
                continue
            summary = _compact_summary(mem) if compact else _memory_summary(mem)
            summary["via_relation"] = True
            extra.append(summary)
    return extra


def _spread_activation(ctx: ServerContext, seed_ids: list[str]) -> int:
    """Reactivate memories related to the recalled seeds (1 hop).

    Recalling a memory stirs the cluster around it: each seed's relation
    neighbours have their dormancy clock reset (``last_accessed`` refreshed)
    without inflating their access counts. This is how a dormant memory wakes
    when a *different* memory sparks it — the core of the never-forget model.
    Best-effort: any failure is swallowed so retrieval never breaks.
    """
    if not seed_ids:
        return 0
    try:
        relations = RelationManager(ctx.conn)
        seeds = set(seed_ids)
        neighbours: list[str] = []
        for sid in seed_ids:
            for other_id in relations.related_ids(sid):
                if other_id not in seeds:
                    neighbours.append(other_id)
        return ctx.store.touch_many(neighbours)
    except Exception:  # never break a read on a best-effort reactivation
        logger.warning("spreading activation failed", exc_info=True)
        return 0


# Minimum fused-relevance score for a memory to be surfaced as a near-duplicate
# of an incoming ``memory_store`` payload. 0.5 keeps the signal-to-noise honest:
# trivially-shared tokens (e.g. "the") fall below it while genuine title/topic
# overlap clears it. Tunable from feel as we accumulate data.
_DEDUPE_MIN_SCORE = 0.5
_DEDUPE_LIMIT = 3

# Minimum fused-relevance score for a memory to be surfaced as a relation
# candidate. Softer than ``_DEDUPE_MIN_SCORE`` because suggesting a *relation*
# is a weaker claim than flagging a *duplicate* — moderate topical overlap is
# enough to be worth a `memory_relate` nudge.
_RELATION_MIN_SCORE = 0.3
_RELATION_LIMIT = 3


def _find_similar(
    ctx: ServerContext,
    *,
    namespace_id: str,
    title: str,
    content: str,
) -> list[dict]:
    """Return up to ``_DEDUPE_LIMIT`` existing memories in ``namespace_id`` that
    look like near-duplicates of a new (``title``, ``content``) payload.

    Uses the existing hybrid BM25+semantic search over the namespace and keeps
    hits above ``_DEDUPE_MIN_SCORE``. Best-effort: any failure is swallowed so
    the store itself never breaks on a dedup-hint error.
    """
    query = f"{title} {content}".strip()
    if not query:
        return []
    try:
        hits = search_mod.search(
            ctx.conn,
            query=query,
            namespace_id=namespace_id,
            limit=_DEDUPE_LIMIT,
            embedder=ctx.store.embedder,
        )
    except Exception:  # never block a store on a hint failure
        logger.warning("dedupe hint search failed", exc_info=True)
        return []
    return [_memory_summary(m) for m in hits if (m.score or 0.0) >= _DEDUPE_MIN_SCORE]


def _suggest_relations(
    ctx: ServerContext,
    *,
    memory_id: str | None,
    namespace_id: str,
    title: str,
    content: str,
    exclude_ids: set[str] | None = None,
) -> list[dict]:
    """Return up to ``_RELATION_LIMIT`` existing memories in ``namespace_id``
    that look like relation candidates for a (``title``, ``content``) payload.

    Excludes ``memory_id`` itself, any ids in ``exclude_ids`` (typically the
    already-surfaced ``similar_memories``), and any memory already linked to
    ``memory_id`` via an existing relation (either direction). Keeps hits above
    ``_RELATION_MIN_SCORE``. Best-effort: any failure is swallowed so the store
    itself never breaks on a hint error.
    """
    query = f"{title} {content}".strip()
    if not query:
        return []
    skip: set[str] = set(exclude_ids or set())
    if memory_id:
        skip.add(memory_id)
        try:
            for other_id in RelationManager(ctx.conn).related_ids(memory_id):
                skip.add(other_id)
        except Exception:
            logger.warning("relation lookup failed in suggestion hint", exc_info=True)
    try:
        # Pull a few extra so post-filtering by skip-set still leaves a useful
        # number of candidates.
        hits = search_mod.search(
            ctx.conn,
            query=query,
            namespace_id=namespace_id,
            limit=_RELATION_LIMIT + len(skip),
            embedder=ctx.store.embedder,
        )
    except Exception:
        logger.warning("relation hint search failed", exc_info=True)
        return []
    out: list[dict] = []
    for mem in hits:
        if mem.id in skip:
            continue
        if (mem.score or 0.0) < _RELATION_MIN_SCORE:
            continue
        out.append(_memory_summary(mem))
        if len(out) >= _RELATION_LIMIT:
            break
    return out
