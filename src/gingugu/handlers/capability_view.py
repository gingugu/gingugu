"""Read-side wiring for capability pointers: the ``capability`` block on a
payload, and the ``memory_recall`` lane.

Kept out of ``summaries.py`` on purpose: those shapers are pure functions of a
``Memory``, while resolving a relative ``path`` needs the namespace's repo path
and the transport, both of which live on the ``ServerContext``.
"""

from __future__ import annotations

from .. import capability
from ..models import Memory, MemoryType
from . import ServerContext
from .summaries import _compact_summary, _memory_summary


def stamp_capabilities(ctx: ServerContext, summaries: list[dict]) -> None:
    """Add ``capability`` to every capability-type summary, in place.

    Ordinary memories are untouched. The namespace paths are looked up once per
    call, and only when a capability is actually present.
    """
    caps = [s for s in summaries if s.get("type") == capability.CAPABILITY_TYPE]
    if not caps:
        return
    paths = {n.id: n.path for n in ctx.namespaces.list()}
    local = ctx.transport == "stdio"
    for summary in caps:
        mem = ctx.store.get(summary["id"], record_access=False)
        block = capability.read(mem.metadata) if mem else None
        if block is not None:
            summary["capability"] = capability.describe(
                block, base=paths.get(summary.get("namespace_id")), local=local
            )


def lane_entries(
    ctx: ServerContext,
    query: str,
    *,
    namespace_id: str | list[str] | None,
    exclude: set[str],
) -> list[dict]:
    """Compact entries for the capabilities that clear the lane's bar.

    Never credited as an access, spread from, or logged: the lane is an
    unasked-for extra, not something the caller chose.
    """
    hits = capability.lane(
        ctx.conn, ctx.store.embedder, query, namespace_id=namespace_id, exclude=exclude
    )
    entries: list[dict] = []
    for mem_id, sim in hits:
        mem = ctx.store.get(mem_id, record_access=False)
        if mem is None:
            continue
        mem.tags = ctx.store.get_tags(mem_id)
        entry = _compact_summary(mem)
        entry["similarity"] = round(sim, 4)
        entries.append(entry)
    if entries:
        names = {n.id: n.name for n in ctx.namespaces.list()}
        for entry in entries:
            entry["namespace"] = names.get(entry["namespace_id"], entry["namespace_id"])
        stamp_capabilities(ctx, entries)
    return entries


def summary_with_capability(ctx: ServerContext, mem: Memory) -> dict:
    """The full write-response summary of ``mem``, with its capability block."""
    summary = _memory_summary(mem)
    stamp_capabilities(ctx, [summary])
    return summary


def write_error(
    existing: Memory, new_type: MemoryType | None, metadata: object, coerced: str | None
) -> str | None:
    """Capability validation of an update's FINAL state, or None when it is valid.

    ``metadata`` omitted keeps the stored blob; ``""`` clears it; anything else
    replaces it. The type is the new one when given, else the existing one.
    """
    final_type = new_type.value if new_type else existing.type.value
    final_meta = existing.metadata if metadata is None else (coerced or None)
    return capability.check(final_type, final_meta)
