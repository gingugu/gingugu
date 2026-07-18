"""Golden-set dataset schema: load + validate.

A dataset is a JSON file with hand-labeled questions. Two shapes:

- **Fixture dataset**: carries a ``memories`` list; questions reference
  memories by their ``key``. The runner builds an ephemeral DB from them.
- **Real-brain dataset**: no ``memories``; questions reference real
  memory UUIDs in the target DB. Keep these under ``bench/local/``
  (gitignored) — they describe a private brain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_VALID_KINDS = ("single", "multi")
_VALID_TYPES = (
    "fact",
    "decision",
    "pattern",
    "bug",
    "architecture",
    "preference",
    "workflow",
    "context",
)
_VALID_CONFIDENCES = ("verified", "inferred", "stale", "deprecated")


@dataclass(frozen=True)
class FixtureMemory:
    """A synthetic memory the runner will insert into an ephemeral DB."""

    key: str
    namespace: str
    type: str
    title: str
    content: str
    confidence: str = "verified"
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Question:
    """One golden question: a query plus the ids/keys that should surface."""

    id: str
    query: str
    relevant: list[str]
    namespaces: list[str] = field(default_factory=list)
    kind: str = "single"
    notes: str | None = None


@dataclass(frozen=True)
class GoldenDataset:
    name: str
    description: str
    questions: list[Question]
    memories: list[FixtureMemory] = field(default_factory=list)

    @property
    def is_fixture(self) -> bool:
        return bool(self.memories)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"invalid dataset: {message}")


def load_dataset(path: Path) -> GoldenDataset:
    """Load and validate a golden-set JSON file. Raises ValueError on bad data."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    _require(raw.get("version") == 1, f"unsupported version {raw.get('version')!r}")

    memories = [FixtureMemory(**m) for m in raw.get("memories", [])]
    keys = [m.key for m in memories]
    _require(len(keys) == len(set(keys)), "duplicate memory keys")
    for m in memories:
        _require(m.type in _VALID_TYPES, f"memory {m.key!r}: bad type {m.type!r}")
        _require(
            m.confidence in _VALID_CONFIDENCES,
            f"memory {m.key!r}: bad confidence {m.confidence!r}",
        )
        _require(bool(m.title and m.content and m.namespace), f"memory {m.key!r}: empty field")

    questions = [Question(**q) for q in raw.get("questions", [])]
    _require(bool(questions), "no questions")
    qids = [q.id for q in questions]
    _require(len(qids) == len(set(qids)), "duplicate question ids")
    key_set = set(keys)
    for q in questions:
        _require(bool(q.query.strip()), f"question {q.id!r}: empty query")
        _require(bool(q.relevant), f"question {q.id!r}: no relevant ids")
        _require(q.kind in _VALID_KINDS, f"question {q.id!r}: bad kind {q.kind!r}")
        if memories:
            missing = [r for r in q.relevant if r not in key_set]
            _require(not missing, f"question {q.id!r}: unknown memory keys {missing}")

    return GoldenDataset(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        questions=questions,
        memories=memories,
    )
