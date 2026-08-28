"""Relation tool handlers: create, enumerate, and repair graph edges."""

from __future__ import annotations

import logging

from ..relations import RelationManager
from . import ServerContext
from .helpers import _err, _single_namespace_not_found
from .relation_ops import MAX_BATCH_EDGES, _apply, _parse_edges, _parse_type

logger = logging.getLogger(__name__)

__all__ = ["MAX_BATCH_EDGES", "register"]


def register(mcp, ctx: ServerContext) -> None:
    relations = RelationManager(ctx.conn)

    @mcp.tool()
    def memory_relate(
        source_id: str,
        target_id: str,
        relation_type: str,
    ) -> dict:
        """Create a directional link between two memories. Relations are used by
        spreading activation (recalling one memory wakes its related cluster) and are
        returned when include_related=True in memory_recall.

        **An edge must encode something search cannot infer.** Recall already ranks
        by hybrid text + semantic similarity, so "these two memories are about the
        same topic" is knowledge the index has for free. What only a relation can
        record is direction and time: which memory REPLACED which, what CAUSED what,
        what CONTRADICTS what, what CONTAINS what. Prefer, in this order:
        ``supersedes``, ``contradicts``, ``caused_by``, ``parent_of``/``child_of``.
        Reach for ``related_to`` only when a genuine connection exists that none of
        those describe - it is the fallback, not the default.

        Quality over volume: spreading activation surfaces at most 3 neighbours per
        seed memory, and it weights by relation type - a directional edge outranks
        ``related_to``, so on any memory with more than 3 edges the ``related_to``
        ones are what lose their slot. A vague edge is therefore not merely
        low-value, it is likely to never fire at all; and precise edges still
        compete against each other for those 3 slots, so a handful of them
        retrieves better than a dense mesh. If you cannot name the directional fact
        an edge records, do not create it.

        A mislabelled edge is repairable: ``memory_unrelate`` retypes or removes one.

        ``source_id`` is the memory making the claim about ``target_id``. ``relation_type``
        must be one of: supersedes (source replaces target), contradicts (conflicting
        claims), caused_by (source was caused by target), parent_of (source contains
        target), child_of (source belongs to target), related_to (fallback: a real
        connection none of the above captures)."""
        try:
            rel = _parse_type(relation_type)
            result = relations.relate(source_id=source_id, target_id=target_id, relation_type=rel)
            return {"ok": True, "relation": result}
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            logger.exception("memory_relate failed")
            return _err(f"memory_relate failed: {exc}")

    @mcp.tool()
    def memory_edges(
        namespace: str | None = None,
        relation_type: str | None = None,
        memory_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Enumerate graph edges with both endpoints resolved to titles. Read-only.

        ``memory_stats`` reports that the graph is, say, 70% ``related_to`` — this is
        how you see WHICH edges those are, in order to judge them. Pair it with
        ``memory_unrelate`` to run a repair sweep: enumerate a page, decide each edge
        on its merits, submit the batch, advance ``offset``.

        Each row carries both endpoints' ids, titles and namespaces, the relation
        type, and each endpoint's ``degree`` (total edges touching it). Degree is the
        one that decides reachability: spreading activation visits at most 3
        neighbours per seed, so edges on a high-degree memory may never fire. It
        ranks candidates by confidence then relation type, so the ones dropped
        there are ``related_to`` first - which is what makes a high-degree,
        mostly-``related_to`` memory the best target for a repair sweep.

        ``namespace`` matches an edge when **either** endpoint lives there, since
        relations legitimately cross namespaces. ``relation_type`` filters to one
        type (``related_to`` is the usual repair target). ``memory_id`` returns every
        edge touching one memory, in either direction. Ordering is stable, so a paged
        sweep sees each edge exactly once — but note that repairing edges as you page
        changes what matches, so re-run from ``offset=0`` when filtering on a type
        you are actively retyping away from."""
        try:
            rel = _parse_type(relation_type) if relation_type else None
            ns_id = None
            ns_name = None
            if namespace:
                ns_name = ctx.namespaces.resolve_name(namespace)
                ns = ctx.namespaces.get(ns_name)
                if ns is None:
                    return _single_namespace_not_found(ns_name)
                ns_id = ns.id
            if limit < 1:
                return _err("limit must be at least 1")
            if offset < 0:
                return _err("offset cannot be negative")

            result = relations.list_edges(
                namespace_id=ns_id,
                relation_type=rel,
                memory_id=memory_id,
                limit=limit,
                offset=offset,
            )
            return {"ok": True, "namespace": ns_name, **result}
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            logger.exception("memory_edges failed")
            return _err(f"memory_edges failed: {exc}")

    @mcp.tool()
    def memory_unrelate(
        source_id: str | None = None,
        target_id: str | None = None,
        relation_type: str | None = None,
        new_relation_type: str | None = None,
        reverse: bool = False,
        edges: list[dict] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Repair the graph: retype a mislabelled edge, turn a backwards one around, or
        remove one that should not exist. The counterpart to ``memory_relate`` — without
        it, an edge written in haste is permanent, and every wrong edge keeps competing
        for one of the 3 spreading-activation slots on its memories forever.

        **Retype** by passing ``new_relation_type`` alongside ``relation_type``. The
        edge is relabelled in place: direction, creation time and metadata survive,
        because the usual repair is "right connection, wrong label" and the graph
        should keep an honest record of when the link was first drawn. If an edge of
        the new type already joins the pair, the two collapse into one and the
        outcome reports ``merged`` rather than ``retyped`` — the edge count drops by
        one, and nothing is fabricated to hide that.

        **Reverse** by passing ``reverse=True`` alongside ``relation_type``. The
        endpoints are swapped on the same row, so id, creation time and metadata
        survive exactly as they do for a retype — the connection was right, only the
        arrow pointed the wrong way. Reversing COMBINES with ``new_relation_type``, in
        one write, because an edge recorded backwards is often mislabelled as well.
        Note that reversing ``parent_of``/``child_of`` is the same operation as flipping
        between the two types: do one or the other, not both. As with a retype, an
        existing edge in the target direction absorbs this one and reports ``merged``.

        **Delete** by omitting ``new_relation_type`` and ``reverse``. With ``relation_type``, only
        that edge goes; without it, every edge from ``source_id`` to ``target_id``
        goes, whatever the type. Deletion here is not the bulk prune the graph
        guidance warns against: the caller names each edge, exactly as
        ``memory_forget`` names a memory.

        **Batch** by passing ``edges`` — an array of up to 100 objects, each with
        ``source_id``, ``target_id`` and optionally ``relation_type`` /
        ``new_relation_type`` / ``reverse``, i.e. the same decision made once per edge:

            [{"source_id": "a", "target_id": "b",
              "relation_type": "related_to", "new_relation_type": "caused_by"},
             {"source_id": "c", "target_id": "d",
              "relation_type": "caused_by", "reverse": true},
             {"source_id": "e", "target_id": "f", "relation_type": "related_to"}]

        A batch is reviewed decisions submitted together, NOT a criteria-driven
        sweep, and that is deliberate. There is no "retype every ``related_to`` in
        this namespace" option, because the whole point of retyping is that each edge
        deserves a different type based on what it actually records — a blanket
        relabel would manufacture directional claims that were never true, and a
        false ``caused_by`` is worse than an honest ``related_to``.

        The batch is validated in full before anything is written, so a malformed op
        fails the whole call rather than leaving the graph half-repaired. Individual
        outcomes (``retyped``, ``reversed``, ``merged``, ``deleted``, ``not_found``,
        ``unchanged``) are reported per edge. Use ``dry_run=True`` to preview a sweep
        first; nothing is written and each op reports what it would have done.

        Find the edges to repair with ``memory_edges``."""
        try:
            if edges is not None:
                if source_id or target_id or relation_type or new_relation_type or reverse:
                    return _err(
                        "pass either `edges` for a batch or the single-edge fields, not both"
                    )
                ops = _parse_edges(edges)
            else:
                if not source_id or not target_id:
                    return _err("source_id and target_id are required (or pass `edges`)")
                if source_id == target_id:
                    return _err("a memory cannot relate to itself, so there is no edge to repair")
                if new_relation_type and not relation_type:
                    return _err("retyping needs relation_type (the current type) as well")
                if reverse and not relation_type:
                    return _err("reversing needs relation_type (the current type) as well")
                ops = [
                    {
                        "source_id": source_id,
                        "target_id": target_id,
                        "relation_type": _parse_type(relation_type) if relation_type else None,
                        "new_relation_type": (
                            _parse_type(new_relation_type) if new_relation_type else None
                        ),
                        "reverse": reverse,
                    }
                ]

            results = [_apply(relations, op, dry_run=dry_run) for op in ops]
            counts: dict[str, int] = {}
            for r in results:
                counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
            return {
                "ok": True,
                "dry_run": dry_run,
                "processed": len(results),
                "outcomes": counts,
                "results": results,
            }
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            logger.exception("memory_unrelate failed")
            return _err(f"memory_unrelate failed: {exc}")
