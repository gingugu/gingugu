"""Shared SQL building blocks for the search modules.

Column lists and WHERE-fragment builders used by both the hybrid engine
(``search.py``) and the filtered listing path (``search_filters.py``).
Fragments are built against a ``memories`` alias passed by the caller so
the same filters compose with the FTS5 join, the embeddings join, and
plain table scans.
"""

from __future__ import annotations

from .claim_queries import claim_filter
from .graph_stats import orphan_filter
from .models import CONFIDENCE_RANK, Confidence, memory_columns_sql, normalize_tag

COLUMNS = memory_columns_sql("m.")

BASE_COLUMNS = memory_columns_sql()


def confidence_filter(column: str, min_confidence: Confidence) -> tuple[str, list[object]]:
    """WHERE fragment keeping only confidences at or above the minimum rank."""
    min_rank = CONFIDENCE_RANK[min_confidence.value]
    allowed = [name for name, rank in CONFIDENCE_RANK.items() if rank >= min_rank]
    placeholders = ", ".join("?" for _ in allowed)
    return f"{column} IN ({placeholders})", list(allowed)


def namespace_filter(column: str, namespace_id: str | list[str]) -> tuple[str, list[object]]:
    """WHERE fragment scoping to one namespace id or any of several.

    Callers must not pass an empty list — resolve/validate namespace names
    before building the query.
    """
    if isinstance(namespace_id, str):
        return f"{column} = ?", [namespace_id]
    placeholders = ", ".join("?" for _ in namespace_id)
    return f"{column} IN ({placeholders})", list(namespace_id)


def tag_filter(column: str, tags: list[str]) -> tuple[str, list[object]]:
    """WHERE fragment requiring a memory to carry *all* given tags."""
    names = list(dict.fromkeys(normalize_tag(t) for t in tags if t.strip()))
    placeholders = ", ".join("?" for _ in names)
    clause = (
        f"{column} IN (SELECT mt.memory_id FROM memory_tags mt "
        f"JOIN tags t ON t.id = mt.tag_id WHERE t.name IN ({placeholders}) "
        f"GROUP BY mt.memory_id HAVING COUNT(DISTINCT t.name) = ?)"
    )
    return clause, [*names, len(names)]


def build_filters(
    *,
    alias: str = "m",
    namespace_id: str | list[str] | None = None,
    type: str | None = None,
    min_confidence: Confidence | None = None,
    include_deprecated: bool = False,
    created_after: str | None = None,
    created_before: str | None = None,
    tags: list[str] | None = None,
    claims: str | None = None,
    orphans: bool = False,
    pinned: bool | None = None,
) -> tuple[list[str], list[object]]:
    """Compose the standard metadata filters into WHERE fragments + params.

    ``claims`` is "open" or "contradicted" — see ``claim_queries.claim_filter``.
    ``orphans`` keeps only memories with no edges — see
    ``graph_stats.orphan_filter``. ``pinned`` is tri-state: None ignores the
    flag, True keeps only pins, False keeps only unpinned. None of the three
    contributes parameters, so all compose anywhere the others do.
    """
    where: list[str] = []
    params: list[object] = []
    if namespace_id is not None:
        clause, ns_params = namespace_filter(f"{alias}.namespace_id", namespace_id)
        where.append(clause)
        params.extend(ns_params)
    if type is not None:
        where.append(f"{alias}.type = ?")
        params.append(type)
    if not include_deprecated:
        where.append(f"{alias}.confidence != 'deprecated'")
    if min_confidence is not None:
        clause, conf_params = confidence_filter(f"{alias}.confidence", min_confidence)
        where.append(clause)
        params.extend(conf_params)
    if created_after:
        where.append(f"{alias}.created_at >= ?")
        params.append(created_after)
    if created_before:
        where.append(f"{alias}.created_at <= ?")
        params.append(created_before)
    if tags:
        clause, tag_params = tag_filter(f"{alias}.id", tags)
        where.append(clause)
        params.extend(tag_params)
    if claims:
        where.append(claim_filter(alias, claims))
    if orphans:
        where.append(orphan_filter(alias))
    if pinned is not None:
        where.append(f"{alias}.pinned = {1 if pinned else 0}")
    return where, params
