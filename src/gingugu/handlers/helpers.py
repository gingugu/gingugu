"""Shared helpers for memory tool handlers."""

from __future__ import annotations

import logging

from .. import search as search_mod
from ..models import Memory
from ..relations import RelationManager
from . import ServerContext

logger = logging.getLogger(__name__)


def _err(message: str) -> dict:
    return {"ok": False, "error": message}


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in value.split(",")] if value else []


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


def _collect_related(ctx: ServerContext, seed_ids: list[str]) -> list[dict]:
    """Fetch memories directly related to the seeds, excluding the seeds."""
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
            summary = _memory_summary(mem)
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
