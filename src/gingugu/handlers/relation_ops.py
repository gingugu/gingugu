"""Edge op parsing and dispatch for ``memory_relate`` and ``memory_unrelate``.

Split from ``handlers/relations.py`` to keep that module inside the repo's
size discipline. The handler owns the MCP surface (argument validation and
the tool docstring); this owns turning a reviewed batch into per-edge
outcomes.

A batch is validated in full before anything is written, so a malformed op
fails the whole call rather than leaving the graph half-repaired. The
``memory_relate`` batch additionally applies every op inside one
``transactions.atomic()`` block, so a write failure partway through (not
just a validation failure) rolls back everything already written in that
call.
"""

from __future__ import annotations

from ..models import RelationType
from ..relations import RelationManager
from ..transactions import atomic

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
        reverse = op.get("reverse", False)
        if not isinstance(reverse, bool):
            raise ValueError(f"edges[{i}] has a non-boolean reverse")
        if reverse and not rel_type:
            raise ValueError(f"edges[{i}] reverses without relation_type (the current type)")
        ops.append(
            {
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": _parse_type(rel_type) if rel_type else None,
                "new_relation_type": _parse_type(new_type) if new_type else None,
                "reverse": reverse,
            }
        )
    return ops


def _apply(relations: RelationManager, op: dict, *, dry_run: bool) -> dict:
    """Run one reviewed edge decision. Never raises; the outcome is the report."""
    source_id, target_id = op["source_id"], op["target_id"]
    old_type, new_type = op["relation_type"], op["new_relation_type"]
    result = {"source_id": source_id, "target_id": target_id}

    # Reverse is checked first because it COMBINES with a retype: an edge
    # written backwards is often mislabelled too, and both are one UPDATE.
    if op.get("reverse"):
        if old_type is None:
            return {**result, "outcome": "error", "detail": "reverse needs relation_type"}
        result |= {"relation_type": old_type.value, "reverse": True}
        if new_type is not None:
            result["new_relation_type"] = new_type.value
        if dry_run:
            return {**result, "outcome": "would_reverse"}
        return {
            **result,
            "outcome": relations.reverse_relation(
                source_id=source_id,
                target_id=target_id,
                relation_type=old_type,
                new_type=new_type,
            ),
        }

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


def _parse_relate_batch(relations: RelationManager, parsed: list) -> list[dict]:
    """Validate a ``memory_relate`` batch fully before anything is written.

    Unlike a ``memory_unrelate`` op, every op here needs ``relation_type`` -
    there is no default, since relate always needs to know what edge it is
    creating. Both memories must already exist: a batch is reviewed decisions
    about specific memories, not a spec for minting new ones.
    """
    if not isinstance(parsed, list):
        raise ValueError("edges must be an array of edge objects")
    if not parsed:
        raise ValueError("edges is empty - nothing to do")
    if len(parsed) > MAX_BATCH_EDGES:
        raise ValueError(f"edges holds {len(parsed)} ops; max is {MAX_BATCH_EDGES} per call")

    ops: list[dict] = []
    for i, op in enumerate(parsed):
        if not isinstance(op, dict):
            raise ValueError(f"edges[{i}] must be an object")
        source_id = op.get("source_id")
        target_id = op.get("target_id")
        rel_type = op.get("relation_type")
        if not source_id:
            raise ValueError(f"edges[{i}] needs source_id")
        if not target_id:
            raise ValueError(f"edges[{i}] needs target_id")
        if not rel_type:
            raise ValueError(f"edges[{i}] needs relation_type")
        if source_id == target_id:
            raise ValueError(f"edges[{i}] is a self-edge, which cannot exist")
        try:
            parsed_type = _parse_type(rel_type)
        except ValueError as exc:
            raise ValueError(f"edges[{i}]: {exc}") from exc
        if not relations._exists(source_id):
            raise ValueError(f"edges[{i}] source_id {source_id!r} not found")
        if not relations._exists(target_id):
            raise ValueError(f"edges[{i}] target_id {target_id!r} not found")
        ops.append({"source_id": source_id, "target_id": target_id, "relation_type": parsed_type})
    return ops


def _apply_relate_batch(relations: RelationManager, ops: list[dict]) -> list[dict]:
    """Apply a validated ``memory_relate`` batch in one transaction.

    All ops go through ``RelationManager.relate`` inside a single
    ``atomic()`` block, so a write failure partway through (not just a
    validation failure) rolls back every op already applied in this call.
    """
    results: list[dict] = []
    with atomic(relations):
        for op in ops:
            outcome = relations.relate(
                source_id=op["source_id"],
                target_id=op["target_id"],
                relation_type=op["relation_type"],
            )
            results.append(
                {
                    "source_id": outcome["source_id"],
                    "target_id": outcome["target_id"],
                    "relation_type": outcome["relation_type"],
                    "outcome": "created" if outcome["created"] else "exists",
                }
            )
    return results


def _relate_batch(relations: RelationManager, edges: list) -> dict:
    """Validate and apply a ``memory_relate`` batch; the handler's edges path."""
    ops = _parse_relate_batch(relations, edges)
    results = _apply_relate_batch(relations, ops)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    return {
        "ok": True,
        "processed": len(results),
        "outcomes": counts,
        "results": results,
    }
