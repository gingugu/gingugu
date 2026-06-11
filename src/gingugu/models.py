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
    # Populated from memory_tags on read; not a column on `memories`.
    tags: list[str] = Field(default_factory=list)
    # Populated only on search/recall results; not stored.
    score: float | None = Field(default=None, exclude=True)
