"""Write-time hints returned by ``memory_store`` and ``memory_update``.

Two unasked-for extras on a write: ``similar_memories`` (merge candidates) and
``suggested_relations`` (candidates to examine for a directional edge). Split
out of ``helpers.py`` when that module outgrew the 300-line limit; the seam is
real rather than arbitrary, because everything here answers one question - what
does the caller need to know about what it just wrote?

BOTH HINTS ARE TWO-STAGE, and the stages are not interchangeable:

1. **Find** candidates with hybrid retrieval (``search.py``). Rank-based fusion
   is very good at "what is nearest", which is exactly this step.
2. **Adjudicate** the survivors with an absolute measure (``similarity.py``)
   and gate on THAT. The retrieval score cannot do this job: it encodes rank,
   not magnitude, so its top hit trends to ~1.0 whether the payload has a real
   neighbour or none at all.

Collapsing the two stages is how this surface used to work, and it meant every
single store returned three "similar" memories and three suggestions no matter
what was written - a fixed tax of six candidates to adjudicate by hand, on a
payload that may have had nothing near it in the corpus.
"""

from __future__ import annotations

import logging

from .. import search as search_mod
from ..models import Memory
from ..relations import RelationManager
from ..similarity import (
    DEDUPE_MIN_SIMILARITY,
    RELATION_MIN_SIMILARITY,
    payload_similarity,
)
from . import ServerContext
from .helpers import _compact_summary

logger = logging.getLogger(__name__)

# How many candidates each hint returns, and how deep retrieval looks for them.
# The pool is deliberately wider than the output: stage 2 rejects, so stage 1
# must have something left to hand back after it does.
_DEDUPE_LIMIT = 3
_RELATION_LIMIT = 3
_POOL_MULTIPLIER = 2

# Similarity FINDS the candidate; it is never itself the reason to link. The
# hybrid index already knows which memories are topically adjacent, so an edge
# that encodes only "these two are similar" duplicates the index at the
# caller's expense. What earns an edge is a directional fact similarity cannot
# see: supersedes, contradicts, caused_by, parent_of/child_of. Measured
# 2026-08-04, before this framing landed: 69% of a 1369-edge real brain was
# `related_to`, and because spreading activation is type-blind those edges were
# out-competing the 31% that carried real signal for a per-seed budget of 3.


def _candidates(
    ctx: ServerContext,
    *,
    namespace_id: str,
    query: str,
    pool: int,
) -> list[Memory]:
    """Stage 1: hybrid retrieval. Best-effort - a hint must never break a write."""
    try:
        return search_mod.search(
            ctx.conn,
            query=query,
            namespace_id=namespace_id,
            limit=pool,
            embedder=ctx.store.embedder,
        )
    except Exception:
        logger.warning("hint retrieval failed", exc_info=True)
        return []


def _adjudicate(
    ctx: ServerContext,
    *,
    title: str,
    content: str,
    hits: list[Memory],
    cutoffs: dict[str, float],
    limit: int,
) -> list[dict]:
    """Stage 2: rescore ``hits`` absolutely, gate, and compact.

    The emitted ``similarity`` REPLACES the retrieval ``score`` rather than
    joining it. Two numbers under one payload, one of them meaningless, is
    worse than one number that means what it says.
    """
    if not hits:
        return []
    try:
        scores, basis = payload_similarity(
            ctx.conn,
            ctx.store.embedder,
            title=title,
            content=content,
            memory_ids=[m.id for m in hits],
        )
    except Exception:
        logger.warning("hint adjudication failed", exc_info=True)
        return []
    floor = cutoffs[basis]
    out: list[dict] = []
    # Retrieval order is not similarity order, so rank the survivors by the
    # measure they were actually judged on.
    for mem in sorted(hits, key=lambda m: -scores.get(m.id, 0.0)):
        similarity = scores.get(mem.id)
        if similarity is None or similarity < floor:
            continue
        summary = _compact_summary(mem)
        summary.pop("score", None)
        summary["similarity"] = round(similarity, 4)
        summary["basis"] = basis
        out.append(summary)
        if len(out) >= limit:
            break
    return out


def find_similar(
    ctx: ServerContext,
    *,
    namespace_id: str,
    title: str,
    content: str,
) -> list[dict]:
    """Existing memories in ``namespace_id`` close enough to be near-duplicates
    of a new ``(title, content)`` payload - i.e. worth merging into rather than
    storing beside.

    Returns an EMPTY list when the payload has no close neighbour, which is the
    common case and the whole point: the hint is a signal, and a signal that
    fires on every write carries no information.

    Hits are ``_compact_summary`` - see that function and ``suggest_relations``
    for why hints never carry full bodies.
    """
    query = f"{title} {content}".strip()
    if not query:
        return []
    hits = _candidates(
        ctx,
        namespace_id=namespace_id,
        query=query,
        pool=_DEDUPE_LIMIT * _POOL_MULTIPLIER,
    )
    return _adjudicate(
        ctx,
        title=title,
        content=content,
        hits=hits,
        cutoffs=DEDUPE_MIN_SIMILARITY,
        limit=_DEDUPE_LIMIT,
    )


def suggest_relations(
    ctx: ServerContext,
    *,
    memory_id: str | None,
    namespace_id: str,
    title: str,
    content: str,
    exclude_ids: set[str] | None = None,
) -> list[dict]:
    """Existing memories in ``namespace_id`` worth EXAMINING for a directional
    relationship to a ``(title, content)`` payload.

    These are candidates, not verdicts. Topical overlap is how they are found,
    never a reason in itself to link them - see the note above
    ``_candidates`` for why a similarity-only edge is a net loss. The caller's
    job is to ask whether one of these is the memory the new one *supersedes*,
    *contradicts*, was *caused by*, or belongs under; if the honest answer is
    "no, they are just both about deploys", the correct action is to link
    nothing.

    Excludes ``memory_id`` itself, any ids in ``exclude_ids`` (typically the
    already-surfaced ``similar_memories``), and any memory already linked to
    ``memory_id`` via an existing relation (either direction).

    Hits are ``_compact_summary``. A hint is a pointer, not a payload: the
    caller only needs enough to decide whether to merge, link, or ignore, and
    ``memory_recall`` is one call away when the answer is "look closer". Full
    bodies here cost the caller its context budget on every single write.
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
    # Pull extra so post-filtering by the skip-set still leaves candidates for
    # stage 2 to judge.
    hits = _candidates(
        ctx,
        namespace_id=namespace_id,
        query=query,
        pool=_RELATION_LIMIT * _POOL_MULTIPLIER + len(skip),
    )
    return _adjudicate(
        ctx,
        title=title,
        content=content,
        hits=[m for m in hits if m.id not in skip],
        cutoffs=RELATION_MIN_SIMILARITY,
        limit=_RELATION_LIMIT,
    )
