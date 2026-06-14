"""FTS5 + semantic hybrid search with composite ranking.

A BM25 candidate pool is pulled from FTS5, then re-ranked using Reciprocal
Rank Fusion (RRF) of the BM25 ranking and a semantic similarity ranking
over stored embeddings. The fused relevance feeds the composite decay
score (relevance × freshness × access × confidence). See
docs/architecture.md → Decay Scoring Algorithm.

When no embeddings are available (provider disabled, missing rows, etc.)
the relevance falls back to a rank-based BM25 score — which still fixes
the old normalize_bm25 compression issue because ranks don't squash.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from . import decay
from . import embeddings as emb
from .embeddings import EmbeddingProvider, cosine
from .models import CONFIDENCE_RANK, Confidence, Memory, normalize_tag

logger = logging.getLogger(__name__)

# Pull this many × limit BM25 candidates before composite re-ranking.
_CANDIDATE_MULTIPLIER = 4

# RRF constant. 60 is the canonical value from the original RRF paper.
_RRF_K = 60

_COLUMNS = (
    "m.id, m.namespace_id, m.type, m.title, m.content, m.confidence, m.source, "
    "m.created_at, m.updated_at, m.last_accessed, m.last_confirmed, "
    "m.access_count, m.metadata"
)

_TOKEN_RE = re.compile(r"[^\w]+", re.UNICODE)


def build_match_query(query: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH string.

    Tokens are extracted, double-quoted (so FTS treats them as literals, not
    operators), and joined with ``OR`` — a document matches if it contains *any*
    term, and BM25 ranks documents that match more terms higher. This favors
    recall (a natural-language query never returns nothing just because it
    includes words absent from the corpus). Returns None if no usable tokens.
    """
    tokens = [t for t in _TOKEN_RE.split(query.strip()) if t]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def normalize_bm25(raw: float) -> float:
    """Map SQLite's negative BM25 (more negative = better) to [0, 1].

    Kept for backward compatibility with callers expecting a score-based
    relevance. Suffers from compression: most decent matches cluster near
    1.0, so freshness/confidence can outrank a clearly-better hit. The
    rank-based fusion used by ``search()`` does not have this problem.
    """
    return 1.0 / (1.0 + max(0.0, -raw))


def _rrf_score(rank: int) -> float:
    """Reciprocal Rank Fusion contribution for a single ranking, rank 1-indexed."""
    return 1.0 / (_RRF_K + rank)


def _fuse_ranks(
    bm25_ranks: dict[str, int],
    semantic_ranks: dict[str, int] | None,
) -> dict[str, float]:
    """Combine BM25 and semantic ranks into a unified [0, 1] relevance.

    Items present in both rankings get the additive RRF benefit. Items in
    only one still get a usable score. Output is normalized so the
    theoretical maximum (rank 1 in both) maps to 1.0.
    """
    ids = set(bm25_ranks)
    if semantic_ranks:
        ids = ids.union(semantic_ranks)
    if not ids:
        return {}
    # Max possible: rank 1 in both rankings.
    max_score = 2.0 * _rrf_score(1) if semantic_ranks else _rrf_score(1)
    out: dict[str, float] = {}
    for mid in ids:
        score = 0.0
        if mid in bm25_ranks:
            score += _rrf_score(bm25_ranks[mid])
        if semantic_ranks and mid in semantic_ranks:
            score += _rrf_score(semantic_ranks[mid])
        out[mid] = min(1.0, score / max_score) if max_score else 0.0
    return out


def _confidence_filter(column: str, min_confidence: Confidence) -> tuple[str, list[object]]:
    """WHERE fragment keeping only confidences at or above the minimum rank."""
    min_rank = CONFIDENCE_RANK[min_confidence.value]
    allowed = [name for name, rank in CONFIDENCE_RANK.items() if rank >= min_rank]
    placeholders = ", ".join("?" for _ in allowed)
    return f"{column} IN ({placeholders})", list(allowed)


def _tag_filter(column: str, tags: list[str]) -> tuple[str, list[object]]:
    """WHERE fragment requiring a memory to carry *all* given tags."""
    names = list(dict.fromkeys(normalize_tag(t) for t in tags if t.strip()))
    placeholders = ", ".join("?" for _ in names)
    clause = (
        f"{column} IN (SELECT mt.memory_id FROM memory_tags mt "
        f"JOIN tags t ON t.id = mt.tag_id WHERE t.name IN ({placeholders}) "
        f"GROUP BY mt.memory_id HAVING COUNT(DISTINCT t.name) = ?)"
    )
    return clause, [*names, len(names)]


def search(
    conn: sqlite3.Connection,
    *,
    query: str,
    namespace_id: str | None = None,
    type: str | None = None,
    min_confidence: Confidence | None = None,
    include_deprecated: bool = False,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = 10,
    weights: dict[str, float] | None = None,
    decay_lambda: float = 0.01,
    tags: list[str] | None = None,
    embedder: EmbeddingProvider | None = None,
) -> list[Memory]:
    """FTS5 + semantic hybrid search re-ranked by the composite decay score.

    A BM25 candidate pool is pulled from FTS5, then re-ranked using RRF of
    BM25 rank and (optionally) semantic-similarity rank over stored
    embeddings. When ``weights`` is provided, the fused relevance feeds the
    full composite (relevance × freshness × access × confidence) and results
    are sorted descending. Without ``weights``, the fused relevance is the
    final score. Without ``embedder``, search degrades to rank-based BM25
    only (still better than the old normalized-score path).
    """
    match = build_match_query(query)
    if match is None:
        return []

    where = ["memories_fts MATCH ?"]
    params: list[object] = [match]

    if namespace_id is not None:
        where.append("m.namespace_id = ?")
        params.append(namespace_id)
    if type is not None:
        where.append("m.type = ?")
        params.append(type)
    if not include_deprecated:
        where.append("m.confidence != 'deprecated'")
    if min_confidence is not None:
        clause, conf_params = _confidence_filter("m.confidence", min_confidence)
        where.append(clause)
        params.extend(conf_params)
    if created_after:
        where.append("m.created_at >= ?")
        params.append(created_after)
    if created_before:
        where.append("m.created_at <= ?")
        params.append(created_before)
    if tags:
        clause, tag_params = _tag_filter("m.id", tags)
        where.append(clause)
        params.extend(tag_params)

    sql = (
        f"SELECT {_COLUMNS}, bm25(memories_fts) AS bm25_score "
        "FROM memories_fts "
        "JOIN memories m ON m.rowid = memories_fts.rowid "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY bm25_score "
        "LIMIT ?"
    )
    params.append(max(limit * _CANDIDATE_MULTIPLIER, limit))

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []

    # BM25 ranking: rows are already ordered ascending by bm25_score
    # (more negative = better match), so position is the rank (1-indexed).
    bm25_ranks: dict[str, int] = {row["id"]: i + 1 for i, row in enumerate(rows)}
    candidate_ids = list(bm25_ranks)

    semantic_ranks = _build_semantic_ranks(conn, query, candidate_ids, embedder)
    fused = _fuse_ranks(bm25_ranks, semantic_ranks)

    results: list[Memory] = []
    for row in rows:
        data = {k: row[k] for k in row.keys() if k != "bm25_score"}
        mem = Memory(**data)
        relevance = fused.get(mem.id, 0.0)
        if weights is not None:
            mem.score = decay.score_memory(
                relevance=relevance,
                last_confirmed=mem.last_confirmed,
                updated_at=mem.updated_at,
                created_at=mem.created_at,
                access_count=mem.access_count,
                confidence=mem.confidence.value,
                weights=weights,
                decay_lambda=decay_lambda,
            )
        else:
            mem.score = relevance
        results.append(mem)

    results.sort(key=lambda m: m.score or 0.0, reverse=True)
    return results[:limit]


def _build_semantic_ranks(
    conn: sqlite3.Connection,
    query: str,
    candidate_ids: list[str],
    embedder: EmbeddingProvider | None,
) -> dict[str, int] | None:
    """Return a {memory_id: rank} dict from cosine similarity, or None.

    Returns None if the embedder is missing/disabled, the query can't be
    encoded, or no candidate has a current-dim embedding. Caller treats
    None as "BM25-only ranking."
    """
    if embedder is None or not getattr(embedder, "enabled", False) or not candidate_ids:
        return None
    try:
        query_vec = embedder.encode(query)
    except Exception:  # pragma: no cover - defensive
        logger.exception("query encode failed; falling back to BM25-only")
        return None
    if query_vec is None:
        return None
    placeholders = ", ".join("?" for _ in candidate_ids)
    rows = conn.execute(
        f"SELECT memory_id, embedding FROM memory_embeddings "
        f"WHERE memory_id IN ({placeholders}) AND dim = ?",
        (*candidate_ids, embedder.dim),
    ).fetchall()
    if not rows:
        return None
    sims: list[tuple[str, float]] = []
    for r in rows:
        try:
            vec = emb.unpack(r["embedding"])
        except Exception:  # pragma: no cover - defensive
            continue
        sims.append((r["memory_id"], cosine(query_vec, vec)))
    if not sims:
        return None
    sims.sort(key=lambda x: x[1], reverse=True)
    return {mid: i + 1 for i, (mid, _) in enumerate(sims)}


_BASE_COLUMNS = _COLUMNS.replace("m.", "")


def advanced_search(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    namespace_id: str | None = None,
    type: str | None = None,
    min_confidence: Confidence | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    sort_by: str = "relevance",
    include_deprecated: bool = False,
    limit: int = 10,
    weights: dict[str, float] | None = None,
    decay_lambda: float = 0.01,
    tags: list[str] | None = None,
    embedder: EmbeddingProvider | None = None,
) -> list[Memory]:
    """Filtered search. With a query, delegates to FTS5 + composite ranking;
    without one, lists by metadata filters ordered by ``sort_by``."""
    if query and query.strip():
        results = search(
            conn,
            query=query,
            namespace_id=namespace_id,
            type=type,
            min_confidence=min_confidence,
            include_deprecated=include_deprecated,
            created_after=created_after,
            created_before=created_before,
            limit=max(limit * _CANDIDATE_MULTIPLIER, limit),
            weights=weights if sort_by in ("relevance", "decay_score") else None,
            decay_lambda=decay_lambda,
            tags=tags,
            embedder=embedder,
        )
    else:
        results = _list_by_filters(
            conn,
            namespace_id=namespace_id,
            type=type,
            min_confidence=min_confidence,
            created_after=created_after,
            created_before=created_before,
            include_deprecated=include_deprecated,
            limit=max(limit * _CANDIDATE_MULTIPLIER, limit),
            weights=weights,
            decay_lambda=decay_lambda,
            tags=tags,
        )

    if sort_by in ("relevance", "decay_score"):
        results.sort(key=lambda m: m.score or 0.0, reverse=True)
    elif sort_by == "created":
        results.sort(key=lambda m: m.created_at, reverse=True)
    elif sort_by == "accessed":
        results.sort(key=lambda m: m.last_accessed, reverse=True)
    return results[:limit]


def _list_by_filters(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None,
    type: str | None,
    min_confidence: Confidence | None,
    created_after: str | None,
    created_before: str | None,
    include_deprecated: bool,
    limit: int,
    weights: dict[str, float] | None,
    decay_lambda: float = 0.01,
    tags: list[str] | None = None,
) -> list[Memory]:
    where: list[str] = []
    params: list[object] = []
    if namespace_id is not None:
        where.append("namespace_id = ?")
        params.append(namespace_id)
    if type is not None:
        where.append("type = ?")
        params.append(type)
    if not include_deprecated:
        where.append("confidence != 'deprecated'")
    if min_confidence is not None:
        conf_clause, conf_params = _confidence_filter("confidence", min_confidence)
        where.append(conf_clause)
        params.extend(conf_params)
    if created_after:
        where.append("created_at >= ?")
        params.append(created_after)
    if created_before:
        where.append("created_at <= ?")
        params.append(created_before)
    if tags:
        clause, tag_params = _tag_filter("id", tags)
        where.append(clause)
        params.extend(tag_params)

    clause = f"WHERE {' AND '.join(where)} " if where else ""
    sql = f"SELECT {_BASE_COLUMNS} FROM memories {clause}ORDER BY last_accessed DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    out: list[Memory] = []
    for row in rows:
        mem = Memory(**dict(row))
        # No query → relevance defaults to 0.5 so freshness/confidence drive order.
        mem.score = (
            decay.score_memory(
                relevance=0.5,
                last_confirmed=mem.last_confirmed,
                updated_at=mem.updated_at,
                created_at=mem.created_at,
                access_count=mem.access_count,
                confidence=mem.confidence.value,
                weights=weights,
                decay_lambda=decay_lambda,
            )
            if weights
            else 0.5
        )
        out.append(mem)
    return out
