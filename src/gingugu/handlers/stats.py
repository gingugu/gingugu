"""Health-stats tool handler: ``memory_stats``.

Split out of ``handlers/search.py`` to keep that module under the repo's
300-line limit, mirroring how relation-batch parsing was split out of
``handlers/relations.py`` into ``handlers/relation_ops.py``.
"""

from __future__ import annotations

import logging

from .. import stats as stats_mod
from . import ServerContext
from .helpers import _err, _resolve_namespaces, _single_namespace_not_found, _split_csv

logger = logging.getLogger(__name__)


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool()
    def memory_stats(
        namespace: str | None = None,
        flag_stale: bool = False,
        review_limit: int | None = None,
    ) -> dict:
        """Return health statistics for the memory store. Use to monitor memory growth,
        identify dormant memories, and get a per-namespace breakdown of counts and
        confidence distribution. Call at session start alongside memory_context to assess
        the state of the knowledge base.

        ``namespace`` accepts a single name, a comma-separated list (e.g.
        "crow,my-project"), or omitted for global. A single name and the unscoped
        call keep the shape they always had: ``{"ok": true, "flagged_stale": 0,
        "stats": {...}}``. A comma list instead returns the namespace-independent
        block (``namespaces``, ``access_log_rows``, ``credentials``) exactly once
        under ``global``, plus each namespace's own scoped stats under
        ``by_namespace`` - the session-start protocol's two-call pattern
        (``memory_stats(namespace="crow")`` then one per project) collapsed into
        one call that computes the shared block once instead of once per
        namespace: ``{"ok": true, "flagged_stale": 0, "namespaces": [...],
        "global": {...}, "by_namespace": {"crow": {...}, "<project>": {...}}}``.
        Names are de-duplicated, order-preserving; an unknown name fails the
        whole call and names it, same as ``memory_recall``/``memory_search``.
        ``review_limit`` applies to every namespace's block.

        ``stats.dormant_count`` reports memories untouched for 90+ days — a resting
        signal only, never a confidence change. Dormant memories wake automatically on
        recall via spreading activation. Memory is never auto-forgotten.

        ``stats.size`` is the character cost the counts do not show: ``total_chars``,
        ``mean_chars``, ``pinned_chars`` and ``largest_pinned_chars``.
        ``pinned_chars`` is the one to watch — pins load unconditionally at every
        session start, ahead of and exempt from ranking, so it is the only part of
        the store paid for on every call regardless of relevance.
        ``largest_pinned_chars`` is the skew check: a tier is not described by how
        many pins it holds, and when one pin approaches the tier total, the tier IS
        that pin. Adding well-chosen pins will not fix that; splitting the outlier
        will. Enumerate the tier with ``memory_search(pinned=True)``.

        ``review_limit`` raises the ``review.sample``, ``claims.sample`` and
        ``graph.orphan_sample`` caps (default 5, max 100) so a reconciliation sweep can
        enumerate every flagged memory — pair with memory_search's ``ids`` parameter to
        pull the full bodies.

        ``stats.graph.orphan_sample`` names the memories behind ``graph.orphans``: those
        no relation touches, which spreading activation can never reach. Ordered by
        confidence, then access count, then recency, so the orphans costing the most
        retrieval come first, each row carrying its ``namespace``.
        ``memory_search(orphans=True)`` pulls the same set with full bodies;
        ``memory_relate`` reconnects one — where a directional fact genuinely exists.

        ``stats.claims`` is the state-claim backlog: memories still asserting a PR/MR
        is open. ``claims.sample`` enumerates them, contradicted first, each row
        tagged ``contradicted`` (a later memory in the same namespace already recorded
        that ref as resolved). ``open`` counts every unresolved claim while
        ``open_actionable`` — what the sample lists — excludes claims on deprecated
        memories. ``memory_search(claims="open")`` pulls the same set with full bodies;
        ``memory_update(resolve_claims=...)`` closes them without editing prose.

        ``claims.unverified`` counts refs a memory names without ever saying what
        became of them. It is reported for visibility, not action: those refs assert
        nothing, so they are excluded from ``open`` and from ``sample`` on purpose.
        Read them with ``memory_search(claims="unverified")``.

        ``flag_stale`` is deprecated and ignored — auto-demotion to stale contradicted
        the never-forget model and has been removed. Retained so existing callers do not
        error."""
        try:
            names = list(dict.fromkeys(_split_csv(namespace))) if namespace is not None else []
            if len(names) > 1:
                resolved, error = _resolve_namespaces(ctx, names)
                if error is not None:
                    return error
                global_block = stats_mod.compute_global_stats(ctx.conn)
                by_namespace = {
                    name: stats_mod._compute_namespace_stats(
                        ctx.conn, namespace_id=resolved[name].id, review_limit=review_limit
                    )
                    for name in names
                }
                return {
                    "ok": True,
                    "flagged_stale": 0,
                    "namespaces": names,
                    "global": global_block,
                    "by_namespace": by_namespace,
                }

            ns_id = None
            if namespace is not None:
                ns = ctx.namespaces.get(namespace)
                if ns is None:
                    return _single_namespace_not_found(namespace)
                ns_id = ns.id
            data = stats_mod.compute_stats(ctx.conn, namespace_id=ns_id, review_limit=review_limit)
            return {"ok": True, "flagged_stale": 0, "stats": data}
        except Exception as exc:
            logger.exception("memory_stats failed")
            return _err(f"memory_stats failed: {exc}")
