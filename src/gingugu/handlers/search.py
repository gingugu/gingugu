"""Search tool handler: ``memory_search``.

``memory_stats`` lives in ``handlers/stats.py`` (split out to keep both
modules under the repo's 300-line limit)."""

from __future__ import annotations

import logging

from .. import search_filters as search_mod
from ..claim_queries import CLAIM_FILTERS
from ..models import Confidence, MemoryType
from . import ServerContext
from .helpers import _attach_review_hints, _err, _split_csv, _stamp_namespace_names
from .scope import read_scope, run_widening
from .summaries import _summarizer

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
        compact: bool = False,
        ids: str | None = None,
        claims: str | None = None,
        orphans: bool = False,
        pinned: bool | None = None,
        explain: bool = False,
    ) -> dict:
        """Advanced filtered search across memories with full control over filters and
        sort order. Use when you need to filter by type, date range, confidence level, or
        sort by something other than relevance. Prefer memory_recall when you just have a
        natural-language query and want the best-matching scored results.

        ``ids`` fetches memories by exact ID (comma-separated, e.g. from a
        memory_stats review sample) — the precise-fetch path. When given, every
        other filter is ignored: results come back in the requested order,
        deprecated memories included (you named them), with a ``missing`` list
        for any ID not found.

        All parameters are optional — omitting all returns all memories up to limit.
        ``namespace`` accepts a single name, a comma-separated list (e.g.
        "crow,my-project"), or None to search every namespace; ``limit`` is always the
        total result cap. A multi-namespace response carries ``namespaces`` and stamps
        each memory with its source ``namespace``. A scoped search WITH a ``query``
        that finds nothing reruns once across every namespace, same filters, and
        reports ``widened_from`` plus ``scope: "all"``; a filter-only sweep never
        widens, since "none here" is its answer.
        ``tags`` is comma-separated; all provided tags must match. ``sort_by`` is one of:
        relevance, created, accessed, decay_score. A ``created``/``accessed`` sort
        orders the whole matching corpus before the limit, so it returns the true
        newest (or least recently read) rows and narrowing ``limit`` narrows that
        answer instead of changing it. With a query, that corpus is the keyword
        match set: a date sort asks something relevance cannot answer, so the
        semantic cohort does not vote in it and results carry no ``score``.
        ``confidence`` sets a minimum
        confidence threshold (verified > inferred > stale > deprecated). ``created_after``
        and ``created_before`` accept ISO 8601 date strings (e.g. "2025-01-01").
        ``include_deprecated`` also returns deprecated memories (stale ones are always
        included). ``compact=True`` returns title + a ~200-char ``summary`` instead of
        full content — the right mode for broad sweeps where full bodies would flood
        the client's tool-result budget; pull full bodies with a targeted follow-up.

        ``claims`` restricts results to the reconciliation backlog — memories that
        still assert a PR/MR is open. "open" is every unresolved claim;
        "contradicted" narrows to those a later memory in the same namespace has
        already recorded as resolved, which are answerable immediately from what the
        brain already holds. Composes with every other filter, so
        ``claims="open", namespace="gingugu", sort_by="created"`` is a working
        sweep. Close them out with ``memory_update(resolve_claims=...)``, which
        records the resolution WITHOUT editing the memory's prose.

        "unverified" is a different set and NOT a backlog: memories naming a
        PR/MR whose prose never says what became of it. They assert nothing, so
        they are absent from every ``open`` count and from ``claims.sample``.
        Most narrate work that long since shipped — this filter is how you read
        them, not a queue to work down. Resolve one by naming its ref
        explicitly; ``resolve_claims="all"`` deliberately leaves them alone.

        ``orphans=True`` restricts results to memories no relation touches — the
        graph backlog that ``memory_stats``' ``graph.orphans`` counts. An orphan
        is reachable only by direct search: spreading activation can never wake
        it, so a verified, frequently-recalled orphan is retrieval the graph is
        leaving on the table. Composes with every other filter and works with or
        without a query, so ``orphans=True, namespace="crow", sort_by="accessed"``
        walks the ones costing the most first. Reconnect them with
        ``memory_relate`` — and only where a directional fact exists to record;
        an orphan is better left alone than wired up with an invented edge.

        ``pinned`` filters on the always-present tier: True returns only pins,
        False only unpinned memories, and omitting it ignores the flag. Pins are
        the memories loaded unconditionally at every session start, ahead of and
        exempt from ranking, so the tier is worth auditing on its own — and
        ``memory_context`` cannot do it, since it returns pins mixed into ranked
        buckets, capped by ``limit`` and scoped per namespace. Pair
        ``pinned=True`` with ``namespace=None`` to enumerate every pin in the
        store, which is the read the tier's own curation needs.

        ``explain=True`` adds a ``score_breakdown`` to each hit: the weighted
        terms ``score`` is the sum of. Results with no ranking behind them carry
        none: an ``ids`` fetch and a ``created``/``accessed`` sort were not
        ranked, and a listing with no query scores every row on the same flat
        relevance, which the breakdown shows as an identical relevance term."""
        try:
            id_list = _split_csv(ids)
            if id_list:
                results, missing = search_mod.fetch_by_ids(ctx.conn, id_list)
                ctx.store.load_tags(results)
                summarize = _summarizer(compact=compact, explain=explain)
                summaries = [_attach_review_hints(summarize(m), m) for m in results]
                ctx.store.record_accesses([m.id for m in results])
                _stamp_namespace_names(ctx, summaries)
                payload = {"ok": True, "count": len(results), "memories": summaries}
                if missing:
                    payload["missing"] = missing
                return payload
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
            if claims is not None and claims not in CLAIM_FILTERS:
                return _err(f"invalid claims {claims!r}; expected one of {list(CLAIM_FILTERS)}")

            scope, error = read_scope(ctx, namespace, use_config=False)
            if error is not None:
                return error

            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
            # Only a lookup widens. An empty filter-only sweep ("open claims in
            # gingugu") is a real answer, and padding it with other namespaces'
            # rows would send a reconciliation sweep after the wrong memories.
            results, widened_from = run_widening(
                scope,
                lambda ns_arg: search_mod.advanced_search(
                    ctx.conn,
                    query=query,
                    namespace_id=ns_arg,
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
                    claims=claims,
                    orphans=orphans,
                    pinned=pinned,
                    embedder=ctx.store.embedder,
                ),
                widen=bool(query and query.strip()),
            )
            ctx.store.load_tags(results)
            summarize = _summarizer(compact=compact, explain=explain)
            summaries = [_attach_review_hints(summarize(m), m) for m in results]
            # Credit the returned seeds as a real access (bumps access_count,
            # refreshes last_accessed, writes access_log row). Spreading-
            # activation neighbours are intentionally not credited here —
            # search has no relation traversal.
            ctx.store.record_accesses([m.id for m in results])
            # Every read surface stamps a readable per-memory namespace
            # (matches memory_context).
            _stamp_namespace_names(ctx, summaries)
            payload: dict = {
                "ok": True,
                "count": len(results),
                "memories": summaries,
            }
            if widened_from is not None:
                payload["scope"] = "all"
                payload["widened_from"] = widened_from
            elif len(scope.names) > 1:
                payload["namespaces"] = scope.names
            return payload
        except Exception as exc:
            logger.exception("memory_search failed")
            return _err(f"memory_search failed: {exc}")
