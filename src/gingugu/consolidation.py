"""Consolidation engine — merge, summarize, or deduplicate memory clusters.

Strategies (see docs/architecture.md → memory_consolidate):

- ``merge``       — combine all contents into one new memory (sectioned).
- ``summarize``   — one new memory with a compact bulleted digest.
- ``deduplicate`` — keep the single best memory, fold the rest into it.

``keep_originals`` (default True) marks originals ``deprecated`` and links them
via ``supersedes`` edges; when False, originals are hard-deleted. This is the
only place memories are intentionally mutated/removed, and only on explicit
``memory_consolidate`` calls.
"""

from __future__ import annotations

import logging
from collections import Counter

from .models import CONFIDENCE_RANK, Confidence, Memory, MemoryType, RelationType
from .relations import RelationManager
from .storage import MemoryStore

logger = logging.getLogger(__name__)

_SUMMARY_SNIPPET = 160


def _load(store: MemoryStore, memory_ids: list[str]) -> list[Memory]:
    memories: list[Memory] = []
    for mid in memory_ids:
        mem = store.get(mid, record_access=False)
        if mem is None:
            raise ValueError(f"memory {mid!r} not found")
        memories.append(mem)
    return memories


def _dominant_type(memories: list[Memory]) -> MemoryType:
    counts = Counter(m.type.value for m in memories)
    return MemoryType(counts.most_common(1)[0][0])


def _max_confidence(memories: list[Memory]) -> Confidence:
    best = max(memories, key=lambda m: CONFIDENCE_RANK[m.confidence.value])
    return best.confidence


def _union_tags(memories: list[Memory]) -> list[str]:
    seen: dict[str, None] = {}
    for mem in memories:
        for tag in mem.tags:
            seen.setdefault(tag, None)
    return list(seen.keys())


def _best(memories: list[Memory]) -> Memory:
    """Highest confidence, tie-broken by most recently updated."""
    return max(
        memories,
        key=lambda m: (CONFIDENCE_RANK[m.confidence.value], m.updated_at),
    )


def _merge_content(memories: list[Memory]) -> str:
    return "\n\n".join(f"## {m.title}\n{m.content}" for m in memories)


def _summary_content(memories: list[Memory]) -> str:
    lines = [f"Digest of {len(memories)} memories:"]
    for m in memories:
        snippet = m.content.strip().replace("\n", " ")
        if len(snippet) > _SUMMARY_SNIPPET:
            snippet = snippet[:_SUMMARY_SNIPPET].rstrip() + "…"
        lines.append(f"- {m.title}: {snippet}")
    return "\n".join(lines)


def _retire_originals(
    store: MemoryStore,
    relations: RelationManager,
    new_id: str,
    originals: list[Memory],
    keep_originals: bool,
) -> None:
    for mem in originals:
        if keep_originals:
            relations.relate(
                source_id=new_id, target_id=mem.id, relation_type=RelationType.SUPERSEDES
            )
            store.update(mem.id, confidence=Confidence.DEPRECATED)
        else:
            store.delete(mem.id)


def consolidate(
    store: MemoryStore,
    relations: RelationManager,
    *,
    memory_ids: list[str],
    strategy: str = "merge",
    keep_originals: bool = True,
) -> dict:
    """Consolidate a cluster of memories. Returns the result summary."""
    if len(memory_ids) < 2:
        raise ValueError("consolidation requires at least 2 memory ids")
    if strategy not in ("merge", "summarize", "deduplicate"):
        raise ValueError(f"unknown strategy {strategy!r}")

    memories = _load(store, memory_ids)
    namespaces = {m.namespace_id for m in memories}
    if len(namespaces) > 1:
        raise ValueError("cannot consolidate memories across namespaces")
    namespace_id = namespaces.pop()

    if strategy == "deduplicate":
        kept = _best(memories)
        others = [m for m in memories if m.id != kept.id]
        _retire_originals(store, relations, kept.id, others, keep_originals)
        kept.tags = store.add_tags(kept.id, _union_tags(memories))
        return {
            "strategy": strategy,
            "consolidated_id": kept.id,
            "kept_title": kept.title,
            "retired": [m.id for m in others],
            "kept_originals": keep_originals,
        }

    content = _merge_content(memories) if strategy == "merge" else _summary_content(memories)
    title = f"[{strategy}] {memories[0].title}"
    new_mem = store.create(
        namespace_id=namespace_id,
        type=_dominant_type(memories),
        title=title,
        content=content,
        confidence=_max_confidence(memories),
        source="consolidation",
        tags=_union_tags(memories),
    )
    _retire_originals(store, relations, new_mem.id, memories, keep_originals)
    return {
        "strategy": strategy,
        "consolidated_id": new_mem.id,
        "title": new_mem.title,
        "merged_from": [m.id for m in memories],
        "kept_originals": keep_originals,
    }
