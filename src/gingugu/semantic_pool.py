"""The semantic half of hybrid retrieval: a fixed-size cosine ranking cohort.

Split out of ``search`` when that module crossed the repo's 300-line limit. The
seam is real rather than arbitrary: everything here answers one question - which
memories get a semantic rank, and against which cohort - while ``search`` owns
the BM25 pool, the RRF fusion, and the composite re-rank.
"""

from __future__ import annotations

import logging
import sqlite3

from . import embeddings as emb
from .embeddings import EmbeddingProvider, cosine

logger = logging.getLogger(__name__)

# The semantic cohort is FIXED. It must never scale with `limit`.
#
# A rank only means something against a fixed cohort. These two were
# `limit * 4` and `limit // 2`, which made a memory's semantic rank - and so its
# relevance, and so the result order - a function of how many rows the caller
# asked for. One memory scored 0.9439 / 0.9379 / 0.9245 / 0.9172 on a single
# query against the real brain, varying nothing but `limit`. A caller narrowing
# the ask to be precise got a different and worse answer, the exact inverse of
# the intent.
#
# The values are the geometry at the benchmarked depth: every figure in `bench/`
# comes from ONE call at limit=10 (`DEFAULT_KS = (1, 5, 10)`, `depth = max(ks)`),
# which built a 40-row pool and a 5-entrant cap. Freezing them here leaves a
# limit=10 call identical to before, so the recorded benchmark still describes
# this code - and every other limit now behaves the way the benchmarked one
# already did.
SEMANTIC_COHORT = 40
ENTRANT_CAP = 5

# Cosine floor for memories that enter the fusion WITHOUT a BM25 match.
# Cohort members always keep their semantic rank; this gate only applies
# to purely-semantic entrants, so weak lookalikes can't displace keyword
# matches. Tuned against the real-brain benchmark (bench/).
_SEMANTIC_ENTRY_MIN = 0.55


def semantic_pool(
    conn: sqlite3.Connection,
    query: str,
    filters: list[str],
    filter_params: list[object],
    embedder: EmbeddingProvider | None,
    entrant_cap: int,
    cohort_ids: set[str],
    bm25_ids: set[str],
) -> dict[str, int] | None:
    """Semantic ranking over the BM25 cohort plus qualified entrants.

    Cosine similarity is computed over the whole filtered corpus -
    brute-force on purpose: at personal-brain scale it is faster than
    maintaining a vector index. Every member of ``cohort_ids`` with an
    embedding keeps a semantic rank (never displaced), and memories with
    no BM25 match join the fusion only when their similarity clears
    ``_SEMANTIC_ENTRY_MIN`` - at most ``entrant_cap`` of them - so
    purely-semantic matches surface without weak lookalikes displacing
    keyword matches. Returns None if the embedder is missing/disabled,
    the query can't be encoded, or no filtered memory has a current-dim
    embedding.

    ``bm25_ids`` is the full BM25 pool, which is larger than ``cohort_ids``
    only when the caller asked for more rows than the cohort holds. Those
    extra rows are neither ranked nor treated as entrants: they matched on
    keywords, so scoring them as though they had not would be wrong, and
    admitting them would make the cohort size depend on ``limit`` again.
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
        if r["id"] in cohort_ids:
            candidates.append((r["id"], sim))
        elif r["id"] in bm25_ids:
            continue  # a keyword match beyond the cohort: BM25 rank only
        elif sim >= _SEMANTIC_ENTRY_MIN:
            entrants.append((r["id"], sim))
    entrants.sort(key=lambda x: x[1], reverse=True)
    sims = candidates + entrants[:entrant_cap]
    if not sims:
        return None
    sims.sort(key=lambda x: x[1], reverse=True)
    return {mid: i + 1 for i, (mid, _) in enumerate(sims)}
