"""Edge-repair op parsing and dispatch for ``memory_unrelate``.

Split from ``handlers/relations.py`` to keep that module inside the repo's
size discipline. The handler owns the MCP surface (argument validation and
the tool docstring); this owns turning a reviewed batch into per-edge
outcomes.

A batch is validated in full before anything is written, so a malformed op
fails the whole call rather than leaving the graph half-repaired.
"""

from __future__ import annotations

from ..models import RelationType
from ..relations import RelationManager

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
