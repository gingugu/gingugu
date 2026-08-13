"""Relation tool handlers: create, enumerate, and repair graph edges."""

from __future__ import annotations

import logging

from ..models import RelationType
from ..relations import RelationManager
from . import ServerContext
from .helpers import _err, _single_namespace_not_found

logger = logging.getLogger(__name__)

# A batch is submitted as reviewed, per-edge decisions. Large enough that a
# repair sweep is not death by round-trip, small enough that a runaway caller
# cannot rewrite the graph in one call.
MAX_BATCH_EDGES = 100


def _parse_type(value: str) -> RelationType:
    try:
        return RelationType(value)
    except ValueError as exc:
        raise ValueError(
            f"invalid relation_type {value!r}; expected one of {[r.value for r in RelationType]}"
        ) from exc


def _parse_edges(parsed: list) -> list[dict]:
    """Validate the batch payload up front. Any bad op rejects the whole batch."""
    if not isinstance(parsed, list):
        raise ValueError("edges must be an array of edge objects")
    if not parsed:
        raise ValueError("edges is empty — nothing to do")
    if len(parsed) > MAX_BATCH_EDGES:
        raise ValueError(f"edges holds {len(parsed)} ops; max is {MAX_BATCH_EDGES} per call")

    ops: list[dict] = []
    for i, op in enumerate(parsed):
        if not isinstance(op, dict):
            raise ValueError(f"edges[{i}] must be an object")
        source_id, target_id = op.get("source_id"), op.get("target_id")
        if not source_id or not target_id:
            raise ValueError(f"edges[{i}] needs both source_id and target_id")
        if source_id == target_id:
            raise ValueError(f"edges[{i}] is a self-edge, which cannot exist")
        rel_type = op.get("relation_type")
        new_type = op.get("new_relation_type")
        ops.append(
            {
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": _parse_type(rel_type) if rel_type else None,
                "new_relation_type": _parse_type(new_type) if new_type else None,
            }
        )
    return ops


def _apply(relations: RelationManager, op: dict, *, dry_run: bool) -> dict:
    """Run one reviewed edge decision. Never raises; the outcome is the report."""
    source_id, target_id = op["source_id"], op["target_id"]
    old_type, new_type = op["relation_type"], op["new_relation_type"]
    result = {"source_id": source_id, "target_id": target_id}

    if new_type is not None:
        if old_type is None:
            return {**result, "outcome": "error", "detail": "retype needs relation_type"}
        result |= {"relation_type": old_type.value, "new_relation_type": new_type.value}
        if dry_run:
            return {**result, "outcome": "would_retype"}
        return {
            **result,
            "outcome": relations.retype_relation(
                source_id=source_id, target_id=target_id, old_type=old_type, new_type=new_type
            ),
        }

    if old_type is not None:
        result["relation_type"] = old_type.value
        if dry_run:
            return {**result, "outcome": "would_delete"}
        deleted = relations.delete_relation(
            source_id=source_id, target_id=target_id, relation_type=old_type
        )
        return {**result, "outcome": "deleted" if deleted else "not_found"}

    # No type given: every edge in this direction goes.
    if dry_run:
        return {**result, "outcome": "would_delete_all"}
    removed = relations.delete_edges(source_id=source_id, target_id=target_id)
    return {
        **result,
        "removed_types": removed,
        "outcome": "deleted" if removed else "not_found",
    }


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
        seed memory, and it does NOT weight by relation type. Every low-signal edge
        therefore competes for a slot against a high-signal one, so a handful of
        precise edges retrieves better than a dense mesh of vague ones. If you cannot
        name the directional fact an edge records, do not create it.

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
        neighbours per seed and does not rank them by type, so edges on a
        high-degree memory may never fire no matter how well labelled.

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
        edges: list[dict] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Repair the graph: retype a mislabelled edge, or remove one that should not
        exist. The counterpart to ``memory_relate`` — without it, an edge written in
        haste is permanent, and every wrong edge keeps competing for one of the 3
        spreading-activation slots on its memories forever.

        **Retype** by passing ``new_relation_type`` alongside ``relation_type``. The
        edge is relabelled in place: direction, creation time and metadata survive,
        because the usual repair is "right connection, wrong label" and the graph
        should keep an honest record of when the link was first drawn. If an edge of
        the new type already joins the pair, the two collapse into one and the
        outcome reports ``merged`` rather than ``retyped`` — the edge count drops by
        one, and nothing is fabricated to hide that.

        **Delete** by omitting ``new_relation_type``. With ``relation_type``, only
        that edge goes; without it, every edge from ``source_id`` to ``target_id``
        goes, whatever the type. Deletion here is not the bulk prune the graph
        guidance warns against: the caller names each edge, exactly as
        ``memory_forget`` names a memory.

        **Batch** by passing ``edges`` — an array of up to 100 objects, each with
        ``source_id``, ``target_id`` and optionally ``relation_type`` /
        ``new_relation_type``, i.e. the same decision made once per edge:

            [{"source_id": "a", "target_id": "b",
              "relation_type": "related_to", "new_relation_type": "caused_by"},
             {"source_id": "c", "target_id": "d", "relation_type": "related_to"}]

        A batch is reviewed decisions submitted together, NOT a criteria-driven
        sweep, and that is deliberate. There is no "retype every ``related_to`` in
        this namespace" option, because the whole point of retyping is that each edge
        deserves a different type based on what it actually records — a blanket
        relabel would manufacture directional claims that were never true, and a
        false ``caused_by`` is worse than an honest ``related_to``.

        The batch is validated in full before anything is written, so a malformed op
        fails the whole call rather than leaving the graph half-repaired. Individual
        outcomes (``retyped``, ``merged``, ``deleted``, ``not_found``, ``unchanged``)
        are reported per edge. Use ``dry_run=True`` to preview a sweep first; nothing
        is written and each op reports what it would have done.

        Find the edges to repair with ``memory_edges``."""
        try:
            if edges is not None:
                if source_id or target_id or relation_type or new_relation_type:
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
                ops = [
                    {
                        "source_id": source_id,
                        "target_id": target_id,
                        "relation_type": _parse_type(relation_type) if relation_type else None,
                        "new_relation_type": (
                            _parse_type(new_relation_type) if new_relation_type else None
                        ),
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
