"""FTS5 + semantic hybrid search with composite ranking.

True hybrid retrieval: a BM25 candidate pool is pulled from FTS5 and an
independent semantic candidate pool is pulled by cosine similarity over
stored embeddings for the same filtered corpus. The two rankings are
fused with Reciprocal Rank Fusion (RRF) over their union, so a memory
that matches the query's meaning surfaces even when it shares no
keywords with it. The fused relevance feeds the composite decay score
(relevance × freshness × access × confidence). See
docs/architecture.md → Decay Scoring Algorithm.

When no embeddings are available (provider disabled, missing rows, etc.)
the relevance falls back to a rank-based BM25 score — which still avoids
the old normalize_bm25 compression issue because ranks don't squash.

Filtered listing without a query lives in ``search_filters.py``
(``advanced_search``); shared SQL fragments live in ``search_common.py``.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from . import decay
from . import embeddings as emb
from .embeddings import EmbeddingProvider, cosine
from .models import Confidence, Memory
from .search_common import COLUMNS, build_filters

logger = logging.getLogger(__name__)

# Pull this many × limit candidates per pool before composite re-ranking.
_CANDIDATE_MULTIPLIER = 4

# RRF constant. 60 is the canonical value from the original RRF paper.
_RRF_K = 60

# Cosine floor for memories that enter the fusion WITHOUT a BM25 match.
# BM25 candidates always keep their semantic rank; this gate only applies
# to purely-semantic entrants, so weak lookalikes can't displace keyword
# matches. Tuned against the real-brain benchmark (bench/).
_SEMANTIC_ENTRY_MIN = 0.55

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


def search(
    conn: sqlite3.Connection,
    *,
    query: str,
    namespace_id: str | list[str] | None = None,
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
    """True hybrid search re-ranked by the composite decay score.

    A BM25 candidate pool (FTS5) and an independent semantic candidate
    pool (cosine over stored embeddings, same filters) are fused with RRF
    over their union. When ``weights`` is provided, the fused relevance
    feeds the full composite (relevance × freshness × access × confidence)
    and results are sorted descending. Without ``weights``, the fused
    relevance is the final score. Without ``embedder``, search degrades to
    rank-based BM25 only.
    """
    match = build_match_query(query)
    if match is None:
        return []

    filters, filter_params = build_filters(
        namespace_id=namespace_id,
        type=type,
        min_confidence=min_confidence,
        include_deprecated=include_deprecated,
        created_after=created_after,
        created_before=created_before,
        tags=tags,
    )
    pool_size = max(limit * _CANDIDATE_MULTIPLIER, limit)

    sql = (
        f"SELECT {COLUMNS}, bm25(memories_fts) AS bm25_score "
        "FROM memories_fts "
        "JOIN memories m ON m.rowid = memories_fts.rowid "
        f"WHERE {' AND '.join(['memories_fts MATCH ?', *filters])} "
        "ORDER BY bm25_score "
        "LIMIT ?"
    )
    rows = conn.execute(sql, [match, *filter_params, pool_size]).fetchall()

    # BM25 ranking: rows are already ordered ascending by bm25_score
    # (more negative = better match), so position is the rank (1-indexed).
    bm25_ranks: dict[str, int] = {row["id"]: i + 1 for i, row in enumerate(rows)}

    # Entrant cap scales with retrieval depth: enough room for genuine
    # semantic-only matches, small enough that entrants can't compress the
    # BM25 candidates' semantic ranks into noise (benchmark-tuned).
    entrant_cap = max(1, limit // 2)
    semantic_ranks = _semantic_pool(
        conn, query, filters, filter_params, embedder, entrant_cap, set(bm25_ranks)
    )
    if not bm25_ranks and not semantic_ranks:
        return []
    fused = _fuse_ranks(bm25_ranks, semantic_ranks)

    row_by_id = {row["id"]: row for row in rows}
    missing = [mid for mid in fused if mid not in row_by_id]
    if missing:
        placeholders = ", ".join("?" for _ in missing)
        extra = conn.execute(
            f"SELECT {COLUMNS} FROM memories m WHERE m.id IN ({placeholders})", missing
        ).fetchall()
        row_by_id.update({row["id"]: row for row in extra})

    results: list[Memory] = []
    for mid, relevance in fused.items():
        row = row_by_id.get(mid)
        if row is None:  # pragma: no cover - defensive
            continue
        mem = Memory(**{k: row[k] for k in row.keys() if k != "bm25_score"})
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


def _semantic_pool(
    conn: sqlite3.Connection,
    query: str,
    filters: list[str],
    filter_params: list[object],
    embedder: EmbeddingProvider | None,
    entrant_cap: int,
    bm25_ids: set[str],
) -> dict[str, int] | None:
    """Semantic ranking over BM25 candidates plus qualified entrants.

    Cosine similarity is computed over the whole filtered corpus —
    brute-force on purpose: at personal-brain scale it is faster than
    maintaining a vector index. Every BM25 candidate with an embedding
    keeps a semantic rank (never displaced), and memories with no BM25
    match join the fusion only when their similarity clears
    ``_SEMANTIC_ENTRY_MIN`` — at most ``entrant_cap`` of them — so
    purely-semantic matches surface without weak lookalikes displacing
    keyword matches. Returns None if the embedder is missing/disabled,
    the query can't be encoded, or no filtered memory has a current-dim
    embedding.
    """
    if embedder is None or not getattr(embedder, "enabled", False):
        return None
    try:
        query_vec = embedder.encode(query)
    except Exception:  # pragma: no cover - defensive
        logger.exception("query encode failed; falling back to BM25-only")
        return None
    if query_vec is None:
        return None

    where = " AND ".join(["e.dim = ?", *filters]) if filters else "e.dim = ?"
    rows = conn.execute(
        "SELECT m.id, e.embedding FROM memory_embeddings e "
        "JOIN memories m ON m.id = e.memory_id "
        f"WHERE {where}",
        [embedder.dim, *filter_params],
    ).fetchall()
    if not rows:
        return None

    candidates: list[tuple[str, float]] = []
    entrants: list[tuple[str, float]] = []
    for r in rows:
        try:
            vec = emb.unpack(r["embedding"])
        except Exception:  # pragma: no cover - defensive
            continue
        sim = cosine(query_vec, vec)
        if r["id"] in bm25_ids:
            candidates.append((r["id"], sim))
        elif sim >= _SEMANTIC_ENTRY_MIN:
            entrants.append((r["id"], sim))
    entrants.sort(key=lambda x: x[1], reverse=True)
    sims = candidates + entrants[:entrant_cap]
    if not sims:
        return None
    sims.sort(key=lambda x: x[1], reverse=True)
    return {mid: i + 1 for i, (mid, _) in enumerate(sims)}
