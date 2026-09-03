"""Pydantic data models and enums for Gingugu."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string (timezone-aware)."""
    return datetime.now(UTC).isoformat()


def normalize_tag(name: str) -> str:
    """Normalize a tag: lowercase, trim, collapse internal whitespace to '-'.

    Prevents fragmentation across casing/whitespace variants (see
    docs/architecture.md → tags). E.g. ``"Python Async"`` -> ``"python-async"``.
    """
    return re.sub(r"\s+", "-", name.strip().lower())


def normalize_metadata(metadata: str | None) -> str | None:
    """Validate and canonicalize a metadata payload.

    The schema treats ``metadata`` as a JSON blob, so we enforce that on
    write rather than letting arbitrary strings accumulate. Rules:

    - ``None`` → ``None`` (unchanged).
    - ``""`` → ``None`` (caller convention: empty string clears metadata).
    - Otherwise must parse as a JSON **object** (``{...}``); arrays,
      numbers, strings, booleans, and ``null`` are rejected. Object form
      is what every existing callsite assumes and what future provenance
      fields (``created_by``, ``client``, ``evidence``, …) plug into.
    - Valid input is re-serialized with sorted keys so equivalent payloads
      are stored identically (helps deduplication and diffs).

    Raises ``ValueError`` on invalid JSON or wrong shape.
    """
    if metadata is None:
        return None
    if metadata == "":
        return None
    try:
        parsed = json.loads(metadata)
    except json.JSONDecodeError as e:
        raise ValueError(f"metadata must be valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"metadata must be a JSON object, got {type(parsed).__name__}")
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False)


class MemoryType(StrEnum):
    FACT = "fact"
    DECISION = "decision"
    PATTERN = "pattern"
    BUG = "bug"
    ARCHITECTURE = "architecture"
    PREFERENCE = "preference"
    WORKFLOW = "workflow"
    CONTEXT = "context"


class Confidence(StrEnum):
    VERIFIED = "verified"
    INFERRED = "inferred"
    STALE = "stale"
    DEPRECATED = "deprecated"


class RelationType(StrEnum):
    SUPERSEDES = "supersedes"
    RELATED_TO = "related_to"
    CAUSED_BY = "caused_by"
    CONTRADICTS = "contradicts"
    PARENT_OF = "parent_of"
    CHILD_OF = "child_of"


# Rank order for the "minimum confidence level" filter (higher = more trusted).
# See docs/architecture.md → Confidence ordering.
CONFIDENCE_RANK: dict[str, int] = {
    Confidence.VERIFIED.value: 3,
    Confidence.INFERRED.value: 2,
    Confidence.STALE.value: 1,
    Confidence.DEPRECATED.value: 0,
}

# Retrieval weight per relation type: 1 for edges recording direction or
# causality, 0 for the ``related_to`` fallback. Spreading activation sorts by
# this, so a directional edge cannot be crowded out of a seed's budget by
# topical-adjacency edges the hybrid index already infers for free.
# Two tiers, not six: nothing measured ranks ``supersedes`` above ``caused_by``,
# and inventing that order would encode a guess as a ranking rule.
RELATION_WEIGHT: dict[str, int] = {
    RelationType.SUPERSEDES.value: 1,
    RelationType.CONTRADICTS.value: 1,
    RelationType.CAUSED_BY.value: 1,
    RelationType.PARENT_OF.value: 1,
    RelationType.CHILD_OF.value: 1,
    RelationType.RELATED_TO.value: 0,
}


class Namespace(BaseModel):
    id: str
    name: str
    path: str | None = None
    description: str | None = None
    # The repo a bare "PR #12" means here. None falls back to ``name`` (the
    # one-namespace-per-repo convention); "" declares this namespace is not a
    # repo at all, so bare refs are dropped instead of mis-keyed.
    default_repo: str | None = None
    created_at: str
    updated_at: str


class Memory(BaseModel):
    id: str
    namespace_id: str
    type: MemoryType
    title: str
    content: str
    confidence: Confidence = Confidence.INFERRED
    source: str | None = None
    created_at: str
    updated_at: str
    last_accessed: str
    last_confirmed: str | None = None
    access_count: int = 0
    metadata: str | None = None
    # Pinned memories always load in memory_context, ahead of and exempt from
    # ranking. Reserved for the handful of rules that must never be missing.
    pinned: bool = False
    # Populated from memory_tags on read; not a column on `memories`.
    tags: list[str] = Field(default_factory=list)
    # Populated only on search/recall results; not stored.
    score: float | None = Field(default=None, exclude=True)
    # The weighted terms `score` is the sum of, for the `explain` read mode.
    # Set wherever `score` is set from a composite; None where the score is a
    # bare relevance or absent, because then there is nothing to decompose.
    score_parts: dict[str, float] | None = Field(default=None, exclude=True)


# ``Memory`` fields that are NOT columns on the `memories` table: `tags` is read
# from `memory_tags`, `score`/`score_parts` exist only on a search result.
NON_COLUMN_FIELDS: frozenset[str] = frozenset({"tags", "score", "score_parts"})

# The `memories` table columns, in schema order. THE one canonical list.
#
# Four modules used to keep private copies of this (storage, context,
# search_common, portability) and they drifted the moment `pinned` was added:
# only two copies gained it. Every search path then built `Memory(**row)` from a
# list without `pinned`, so the field fell back to its default and reported a
# confident `False` for genuinely pinned memories - and `memory_export` dropped
# the flag outright, silently unpinning them on a restore. The copies are gone;
# `test_memory_columns.py` holds this tuple against both `Memory` and the live
# SQLite schema, so adding a column without teaching the readers fails CI.
MEMORY_COLUMNS: tuple[str, ...] = (
    "id",
    "namespace_id",
    "type",
    "title",
    "content",
    "confidence",
    "source",
    "created_at",
    "updated_at",
    "last_accessed",
    "last_confirmed",
    "access_count",
    "metadata",
    "pinned",
)


def memory_columns_sql(prefix: str = "") -> str:
    """The column list for a SELECT, optionally table-qualified (e.g. ``"m."``)."""
    return ", ".join(f"{prefix}{column}" for column in MEMORY_COLUMNS)


def memory_placeholders_sql() -> str:
    """The matching ``:name`` placeholder list for an INSERT.

    Generated from the same tuple as the column list so the two can never fall
    out of order or out of step, which is how a hand-written VALUES clause
    silently drops a column.
    """
    return ", ".join(f":{column}" for column in MEMORY_COLUMNS)
