"""Benchmark runner: score a golden dataset against a memory DB.

Mirrors the live ``memory_recall`` retrieval path exactly —
``search.search()`` with composite weights and the configured embedder —
but never mutates the target: no access recording, no dormancy touches. A
benchmark run must not change the ranking signals it measures, and a real
brain is opened read-only at the SQLite level as a hard guarantee.

``measure_spread`` additionally reports what spreading activation would
surface around each question's seeds. It calls the *selection* half of that
path (``dampened_neighbour_ids``, pure SELECT) and never the reactivation
half (``touch_many``), so the no-mutation guarantee is unchanged. Without it
the harness cannot see this path at all: relation traversal is reached only
from ``handlers/helpers.py``, never from ``search()``. Any claim about a
ranking change inside ``dampened_neighbour_ids`` that cites a plain bench run
is measuring code the change cannot reach.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from gingugu import search as search_mod
from gingugu.config import _DEFAULT_WEIGHTS
from gingugu.database import migrate
from gingugu.embeddings import EmbeddingProvider, NullEmbeddingProvider
from gingugu.models import Confidence, MemoryType, RelationType
from gingugu.relations import RelationManager

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


def build_fixture_db(
    dataset: GoldenDataset, *, embedder: EmbeddingProvider | None = None
) -> tuple[sqlite3.Connection, dict[str, str]]:
    """Create an ephemeral in-memory DB from a fixture dataset.

    Returns the connection and a {memory key -> generated uuid} map so
    question labels can be translated to real ids. Embeddings are left
    empty (Null provider, the default) so CI runs are deterministic and
    offline; pass a real ``embedder`` to also exercise hybrid retrieval -
    ``MemoryStore.create`` embeds synchronously, so vectors are ready
    immediately for the search calls that follow.
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
    store = MemoryStore(conn, embedder=embedder or NullEmbeddingProvider())

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

    rel_mgr = RelationManager(conn)
    for fr in dataset.relations:
        rel_mgr.relate(
            source_id=key_to_id[fr.source],
            target_id=key_to_id[fr.target],
            relation_type=RelationType(fr.type),
        )
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
    measure_spread: bool = False,
) -> BenchReport:
    """Run every question through the live recall path and score it.

    Issues one ``search()`` call per cutoff in ``ks`` rather than slicing a
    single deep call - recall@1 must exercise a real ``limit=1`` call, not a
    slice of a ``limit=10`` result. Relevance was a function of ``limit``
    until PR #55; a benchmark that only ever calls at ``max(ks)`` cannot
    catch that class of regression coming back.
    """
    depth = max(ks)
    hybrid = bool(embedder is not None and getattr(embedder, "enabled", False))
    results: list[QuestionResult] = []

    for q in dataset.questions:
        relevant = [key_to_id.get(r, r) for r in q.relevant] if key_to_id else list(q.relevant)
        ns_ids = _namespace_ids(conn, q.namespaces)
        ns_arg = ns_ids if ns_ids is None or len(ns_ids) > 1 else ns_ids[0]

        retrieved_by_k: dict[int, list[str]] = {}
        deepest: list = []
        for k in ks:
            memories_k = search_mod.search(
                conn,
                query=q.query,
                namespace_id=ns_arg,
                limit=k,
                weights=weights,
                decay_lambda=decay_lambda,
                embedder=embedder,
            )
            retrieved_by_k[k] = [m.id for m in memories_k]
            if k == depth:
                deepest = memories_k

        scores: dict[str, float] = {"mrr": mrr(relevant, retrieved_by_k[depth])}
        for k in ks:
            scores[f"recall@{k}"] = recall_at_k(relevant, retrieved_by_k[k], k)
            scores[f"precision@{k}"] = precision_at_k(relevant, retrieved_by_k[k], k)
        if measure_spread:
            # Seeded from the deepest call: that is what the live recall path
            # hands spreading activation.
            scores.update(_spread_composition(conn, retrieved_by_k[depth]))
        tokens = estimate_tokens([f"{m.title}\n{m.content}" for m in deepest])
        results.append(
            QuestionResult(
                id=q.id, kind=q.kind, retrieved=retrieved_by_k[depth], scores=scores, tokens=tokens
            )
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


def _spread_composition(conn: sqlite3.Connection, seed_ids: list[str]) -> dict[str, float]:
    """What spreading activation would surface around these seeds.

    Returns the neighbour count and how many of those neighbours won their
    slot on a directional edge. The share of the budget carried by real signal
    is the quantity type-weighting is meant to move; the count is what says
    whether a share moved because the mix improved or because the
    neighbourhood shrank. Both are reported so neither can be read alone.

    "Directional" is defined here against ``RelationType`` — everything that is
    not ``related_to`` — and deliberately NOT against ``RELATION_WEIGHT``. A
    metric that read the same table the traversal ranks by would move whenever
    that table was tuned, reporting a win for any change to it including one
    that made retrieval worse. The measurement has to survive the knob.
    """
    if not seed_ids:
        return {"spread_extras": 0.0, "spread_high_signal": 0.0}

    neighbours = RelationManager(conn).dampened_neighbour_ids(seed_ids)
    seeds = set(seed_ids)
    high = 0
    for nid in neighbours:
        rows = conn.execute(
            "SELECT relation_type, source_id, target_id FROM relations "
            "WHERE source_id = ? OR target_id = ?",
            (nid, nid),
        ).fetchall()
        # Only edges joining this neighbour to the SEED set can have won it a
        # slot; its other edges are irrelevant to how it was reached.
        joining = [
            r["relation_type"] for r in rows if (r["source_id"] in seeds or r["target_id"] in seeds)
        ]
        if any(t != RelationType.RELATED_TO.value for t in joining):
            high += 1
    return {"spread_extras": float(len(neighbours)), "spread_high_signal": float(high)}


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
