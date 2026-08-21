"""Pydantic data models and enums for Gingugu."""

from __future__ import annotations

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


# ``Memory`` fields that are NOT columns on the `memories` table: `tags` is read
# from `memory_tags`, `score` exists only on a search result.
NON_COLUMN_FIELDS: frozenset[str] = frozenset({"tags", "score"})

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
