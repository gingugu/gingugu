"""Benchmark runner: score a golden dataset against a memory DB.

Mirrors the live ``memory_recall`` retrieval path exactly —
``search.search()`` with composite weights and the configured embedder —
but never mutates the target: no access recording, no spreading
activation, no dormancy touches. A benchmark run must not change the
ranking signals it measures, and a real brain is opened read-only at the
SQLite level as a hard guarantee.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from gingugu import search as search_mod
from gingugu.config import _DEFAULT_WEIGHTS
from gingugu.database import migrate
from gingugu.embeddings import EmbeddingProvider, NullEmbeddingProvider
from gingugu.models import Confidence, MemoryType

from .dataset import GoldenDataset
from .metrics import estimate_tokens, mean, mrr, precision_at_k, recall_at_k

DEFAULT_KS = (1, 5, 10)

# Fixture runs always use the shipped default weights (never env overrides)
# so CI numbers are reproducible on any machine. Kept in lockstep with
# config by importing rather than copying.
FIXTURE_WEIGHTS: dict[str, float] = dict(_DEFAULT_WEIGHTS)


@dataclass(frozen=True)
class QuestionResult:
    id: str
    kind: str
    retrieved: list[str]
    scores: dict[str, float]
    tokens: int


@dataclass(frozen=True)
class BenchReport:
    dataset: str
    mode: str  # "fixture" | "real"
    retrieval: str  # "bm25-only" | "hybrid"
    ks: tuple[int, ...]
    results: list[QuestionResult]
    aggregates: dict[str, float] = field(default_factory=dict)
    by_kind: dict[str, dict[str, float]] = field(default_factory=dict)


def build_fixture_db(dataset: GoldenDataset) -> tuple[sqlite3.Connection, dict[str, str]]:
    """Create an ephemeral in-memory DB from a fixture dataset.

    Returns the connection and a {memory key -> generated uuid} map so
    question labels can be translated to real ids. Embeddings are left
    empty (Null provider) so CI runs are deterministic and offline.
    """
    from gingugu.config import Config
    from gingugu.namespaces import NamespaceManager
    from gingugu.storage import MemoryStore

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)

    cfg = Config(
        db_path=Path(":memory:"),
        namespace=None,
        namespace_path=None,
        auto_context_limit=10,
        decay_lambda=0.01,
    )
    namespaces = NamespaceManager(conn, cfg)
    store = MemoryStore(conn, embedder=NullEmbeddingProvider())

    key_to_id: dict[str, str] = {}
    for fm in dataset.memories:
        ns = namespaces.get_or_create(fm.namespace)
        mem = store.create(
            namespace_id=ns.id,
            type=MemoryType(fm.type),
            title=fm.title,
            content=fm.content,
            confidence=Confidence(fm.confidence),
            tags=fm.tags or None,
        )
        key_to_id[fm.key] = mem.id
    return conn, key_to_id


def open_real_db(path: Path) -> sqlite3.Connection:
    """Open a real brain strictly read-only (SQLite ``mode=ro``)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _namespace_ids(conn: sqlite3.Connection, names: list[str]) -> list[str] | None:
    """Map namespace names to ids; None means 'search everything'."""
    if not names:
        return None
    placeholders = ", ".join("?" for _ in names)
    rows = conn.execute(
        f"SELECT name, id FROM namespaces WHERE name IN ({placeholders})", names
    ).fetchall()
    found = {r["name"]: r["id"] for r in rows}
    missing = [n for n in names if n not in found]
    if missing:
        raise ValueError(f"unknown namespaces in dataset: {missing}")
    return [found[n] for n in names]


def run_benchmark(
    dataset: GoldenDataset,
    conn: sqlite3.Connection,
    *,
    weights: dict[str, float],
    decay_lambda: float,
    embedder: EmbeddingProvider | None = None,
    ks: tuple[int, ...] = DEFAULT_KS,
    key_to_id: dict[str, str] | None = None,
) -> BenchReport:
    """Run every question through the live recall path and score it."""
    depth = max(ks)
    hybrid = bool(embedder is not None and getattr(embedder, "enabled", False))
    results: list[QuestionResult] = []

    for q in dataset.questions:
        relevant = [key_to_id.get(r, r) for r in q.relevant] if key_to_id else list(q.relevant)
        ns_ids = _namespace_ids(conn, q.namespaces)
        memories = search_mod.search(
            conn,
            query=q.query,
            namespace_id=ns_ids if ns_ids is None or len(ns_ids) > 1 else ns_ids[0],
            limit=depth,
            weights=weights,
            decay_lambda=decay_lambda,
            embedder=embedder,
        )
        retrieved = [m.id for m in memories]
        scores: dict[str, float] = {"mrr": mrr(relevant, retrieved)}
        for k in ks:
            scores[f"recall@{k}"] = recall_at_k(relevant, retrieved, k)
            scores[f"precision@{k}"] = precision_at_k(relevant, retrieved, k)
        tokens = estimate_tokens([f"{m.title}\n{m.content}" for m in memories[:depth]])
        results.append(
            QuestionResult(id=q.id, kind=q.kind, retrieved=retrieved, scores=scores, tokens=tokens)
        )

    return BenchReport(
        dataset=dataset.name,
        mode="fixture" if dataset.is_fixture else "real",
        retrieval="hybrid" if hybrid else "bm25-only",
        ks=ks,
        results=results,
        aggregates=_aggregate(results),
        by_kind=_aggregate_by_kind(results),
    )


def _metric_names(results: list[QuestionResult]) -> list[str]:
    return list(results[0].scores) if results else []


def _aggregate(results: list[QuestionResult]) -> dict[str, float]:
    out = {name: mean([r.scores[name] for r in results]) for name in _metric_names(results)}
    if results:
        out["tokens"] = mean([float(r.tokens) for r in results])
    return out


def _aggregate_by_kind(results: list[QuestionResult]) -> dict[str, dict[str, float]]:
    kinds = sorted({r.kind for r in results})
    return {kind: _aggregate([r for r in results if r.kind == kind]) for kind in kinds}
