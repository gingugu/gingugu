"""Auto-context generation for session start (``memory_context``).

Pinned memories load first and unconditionally (see below). Everything else
draws from three intent buckets, each ranked by its *own* native signal and
given a guaranteed quota of the ``limit`` slots, so no one intent can be
starved by the composite-score ranking (see docs/architecture.md →
memory_context):

1. Task-relevant (if ``task_hint``) — FTS5 search scoped to namespace,
   ranked by composite relevance.
2. Recently written in this namespace - by ``updated_at`` (pure write
   recency), excluding deprecated.
3. Cross-namespace high-confidence patterns — pattern/preference + verified,
   ranked by ``access_count``.

Quotas are filled recency-first so a freshly-stored, never-accessed memory
(the "where we left off" signal) always survives the cut. Any slots left after
the guaranteed quotas are backfilled from the combined pool by composite score.

Selection order and presentation order are separate decisions. Quotas are
filled recency-first (a survival question); the result is then presented by
bucket membership - task, recency, cross-namespace, backfill - because the
buckets are not scored on a comparable scale. Only the task bucket has a real
search relevance, so re-sorting the selected set by composite score would rank
a guaranteed recency slot below every task hit, undoing the quota that just
protected it. Within a bucket the scores ARE comparable, and each bucket is
given a deterministic total order so ties never fall to SQLite.

Types ``architecture`` and ``decision`` get a +0.1 score boost (disproportionately
useful at session start).

**Pinned memories are additive to ``limit``, not a share of it.** Ranking
answers "what is most relevant to this task?"; it cannot answer "what must
never be missing?". A pin is the second question, so a pinned memory is exempt
from scoring entirely and is never evicted by a quota. Making pins compete for
a slice of ``limit`` would truncate them under contention — which recreates the
exact failure the tier exists to fix, just with an extra step. The blast radius
is bounded by ``PINNED_HARD_CAP`` instead: pin more than that and the tier
stops being a constitution and starts being another pile.
"""

from __future__ import annotations

import math
import sqlite3

from . import decay, search
from .context_buckets import (
    PINNED_HARD_CAP,
    cross_namespace_patterns,
    recently_active,
)
from .context_buckets import (
    pinned as pinned_bucket,
)
from .embeddings import EmbeddingProvider
from .models import Memory

# Re-exported: ``PINNED_HARD_CAP`` is part of this module's public surface for
# the handlers and tests that enforce the pin budget.
__all__ = ["PINNED_HARD_CAP", "build_context"]

_BOOST_TYPES = {"architecture", "decision"}
_BOOST_AMOUNT = 0.1

# Guaranteed share of the result ``limit`` reserved for each intent bucket.
# Recency is filled first (it's the intent the old score-and-collapse design
# starved); task-relevance is the primary intent when a hint is given;
# cross-namespace wisdom yields first when slots are contended.
_TASK_RATIO = 0.5
_RECENT_RATIO = 0.3
_CROSS_NS_QUOTA = 3


def _score(mem: Memory, weights: dict[str, float], decay_lambda: float, relevance: float) -> float:
    """Composite score without the type boost (applied once, later, for all buckets).

    Also stamps ``score_parts`` so an ``explain`` read can show which term
    carried the memory. Note what the breakdown reveals here: the recency and
    cross-namespace buckets are scored with a synthetic ``relevance``, so their
    relevance term is a constant no matter how well they match anything.
    """
    mem.score_parts = decay.score_parts(
        relevance=relevance,
        last_confirmed=mem.last_confirmed,
        updated_at=mem.updated_at,
        created_at=mem.created_at,
        access_count=mem.access_count,
        confidence=mem.confidence.value,
        weights=weights,
        decay_lambda=decay_lambda,
    )
    return sum(mem.score_parts.values())


def build_context(
    conn: sqlite3.Connection,
    *,
    namespace_id: str,
    task_hint: str | None = None,
    limit: int = 10,
    weights: dict[str, float],
    decay_lambda: float = 0.01,
    embedder: EmbeddingProvider | None = None,
) -> list[Memory]:
    """Assemble the auto-context set via guaranteed per-bucket quotas.

    Each bucket is ranked by its native signal, then a reserved share of the
    ``limit`` slots is taken from each (recency first) so the "where we left
    off" signal can't be evicted by the relevance/access-dominated composite.
    Remaining slots are backfilled from the combined pool by composite score;
    the final list is presented in that selection order, not re-sorted.

    Pinned memories are prepended to that result and are **additive to**
    ``limit`` (up to ``PINNED_HARD_CAP``), so the caller may receive more than
    ``limit`` memories. They are excluded from the ranked buckets so a pin
    never consumes a discovery slot it was going to get for free.
    """
    pinned = pinned_bucket(conn, namespace_id, PINNED_HARD_CAP)
    pinned_ids = {m.id for m in pinned}

    # Bucket 1: task-relevant, already composite-scored and ordered by search().
    task_bucket: list[Memory] = []
    if task_hint and task_hint.strip():
        task_n = max(1, math.ceil(limit * _TASK_RATIO))
        # Over-fetch by the pin count so dropping pins below still leaves a full
        # quota of ranked hits — search has no pinned filter of its own, and
        # LIMIT would otherwise be spent on memories already guaranteed.
        task_bucket = search.search(
            conn,
            query=task_hint,
            namespace_id=namespace_id,
            limit=task_n + len(pinned),
            weights=weights,
            decay_lambda=decay_lambda,
            embedder=embedder,
        )

    # Bucket 2: recently written in this namespace, ordered by updated_at DESC.
    recent_bucket = recently_active(conn, namespace_id, limit)
    for mem in recent_bucket:
        mem.score = _score(mem, weights, decay_lambda, relevance=0.5)

    # Bucket 3: cross-namespace verified patterns/preferences, by access_count.
    cross_bucket = cross_namespace_patterns(conn, exclude_ns=namespace_id)
    for mem in cross_bucket:
        mem.score = _score(mem, weights, decay_lambda, relevance=0.5)

    # De-duplicate across buckets, keeping each memory's highest score (a task
    # hit that also shows up in the recency bucket keeps its richer relevance).
    # Drop pins from the ranked buckets: they are already guaranteed, so
    # leaving them in would spend a discovery slot on a memory the caller was
    # getting for free. Filtered before de-dup so quota selection below can
    # never pick an id that is missing from ``best``.
    task_bucket = [m for m in task_bucket if m.id not in pinned_ids]
    recent_bucket = [m for m in recent_bucket if m.id not in pinned_ids]
    cross_bucket = [m for m in cross_bucket if m.id not in pinned_ids]

    best: dict[str, Memory] = {}
    for mem in (*task_bucket, *recent_bucket, *cross_bucket):
        current = best.get(mem.id)
        if current is None or (mem.score or 0.0) > (current.score or 0.0):
            best[mem.id] = mem

    # Apply the architecture/decision boost exactly once, after de-dup. The
    # boost is uniform, so it never changes which instance won the max above.
    #
    # It is recorded as its own term rather than folded into an existing one:
    # the boost is not a property of the memory's relevance or freshness, it is
    # a thumb on the scale for two types at session start. A breakdown that hid
    # it would leave the terms not summing to the score, which is the one thing
    # a breakdown must never do.
    for mem in best.values():
        if mem.type.value in _BOOST_TYPES and mem.score is not None:
            mem.score += _BOOST_AMOUNT
            if mem.score_parts is not None:
                mem.score_parts = {**mem.score_parts, "type_boost": _BOOST_AMOUNT}

    # Give each SQL-ordered bucket a deterministic total order: native signal
    # first, composite score to break ties, id only as a last resort.
    #
    # Ties on the native signal are routine rather than exotic - memories
    # written in one batch share a timestamp, and on a coarse system clock two
    # separate writes land in the same tick - so without this the order comes
    # from SQLite's rowid on some platforms and from the clock on others.
    #
    # This MUST run after the boost above, not before. The boost is what
    # separates an architecture memory from an otherwise identical fact, so
    # sorting on the pre-boost score leaves them tied and drops through to the
    # id - a random UUID, which is a coin flip per run rather than a tiebreak.
    # Scores are read from ``best`` because that is the instance the boost was
    # applied to; a bucket may hold a different, unboosted instance of the same
    # memory.
    #
    # Comparing scores WITHIN a bucket is sound - every row got its relevance
    # the same way. Comparing them ACROSS buckets is not, which is why this sort
    # is per-bucket and never global.
    # Sorted on the same signal the bucket was SELECTED by (``updated_at``, a
    # write timestamp). Re-sorting it on any other key here would undo the SQL
    # ordering that defines the bucket - which is how the last presentation bug
    # got in.
    recent_bucket.sort(key=lambda m: (m.updated_at, best[m.id].score or 0.0, m.id), reverse=True)
    cross_bucket.sort(key=lambda m: (m.access_count, best[m.id].score or 0.0, m.id), reverse=True)

    # Guaranteed-quota selection. Fill recency FIRST - it's the intent the old
    # score-and-collapse design starved, and filling it first is what stops a
    # contended limit from evicting it. Selection order is a survival question;
    # it is deliberately not the order these are presented in (see below).
    chosen: set[str] = set()
    backfilled: list[str] = []

    def take(bucket: list[Memory], quota: int) -> None:
        taken = 0
        for mem in bucket:
            if len(chosen) >= limit or taken >= quota:
                return
            if mem.id not in chosen:
                chosen.add(mem.id)
                taken += 1

    recent_quota = max(1, math.ceil(limit * _RECENT_RATIO))
    task_quota = max(1, math.ceil(limit * _TASK_RATIO)) if task_bucket else 0

    take(recent_bucket, recent_quota)
    take(task_bucket, task_quota)
    take(cross_bucket, _CROSS_NS_QUOTA)

    # Backfill any unused slots from the combined pool by composite score.
    if len(chosen) < limit:
        leftovers = sorted(
            (m for mid, m in best.items() if mid not in chosen),
            key=lambda m: m.score or 0.0,
            reverse=True,
        )
        for mem in leftovers:
            if len(chosen) >= limit:
                break
            chosen.add(mem.id)
            backfilled.append(mem.id)

    # Presentation order, which is a different question from selection order:
    # answer what was asked first (task), then "where did we leave off"
    # (recency), then cross-namespace wisdom, then the score-ordered backfill.
    # With no task_hint the task bucket is empty and recency leads naturally.
    #
    # Keyed off bucket MEMBERSHIP, not off which quota happened to claim the
    # memory: recency is filled first, so a task-relevant memory that is also
    # recent gets taken by the recency quota and would otherwise be presented as
    # though it had never matched the query at all.
    #
    # Each bucket is emitted in its own native order (search relevance,
    # updated_at, access_count), which is the ordering that actually means
    # something within it.
    #
    # Do NOT re-sort this by composite score. The buckets are scored on
    # incomparable relevance: task hits carry a real search relevance while the
    # recency and cross-namespace buckets carry the ``relevance=0.5``
    # placeholder assigned above, which caps them no matter how fresh they are.
    # A score sort therefore silently undoes the quota that just protected the
    # recency slot, burying the "where we left off" memory in the tail of a long
    # payload - it gets its guaranteed slot and is then shown last, which is the
    # same as not guaranteeing it.
    #
    # Pins lead for that reason and one more: they carry no score at all (they
    # never entered the ranking), so any score-ordered sort sinks them to the
    # bottom - the exact opposite of what a pin means.
    selected: list[str] = []
    seen: set[str] = set()
    for bucket in (task_bucket, recent_bucket, cross_bucket):
        for mem in bucket:
            if mem.id in chosen and mem.id not in seen:
                seen.add(mem.id)
                selected.append(mem.id)
    selected.extend(mid for mid in backfilled if mid not in seen)

    ranked = [best[mid] for mid in selected]
    return [*pinned, *ranked[:limit]]
