"""Absolute similarity between a memory payload and existing memories.

Retrieval and adjudication are two different questions, and this module owns
the second one.

``search.py`` answers "which memories are the best candidates for this query?"
Its fused RRF relevance is excellent at that and useless for anything else: it
is a function of a candidate's RANK in the BM25 and semantic pools, normalized
so that rank 1 in both maps to 1.0. Something is always rank 1, so the top hit
of any non-empty query trends toward 1.0 no matter what was asked. Measured
against a real 1,423-memory brain, the payload "Lunch was a tuna sandwich"
scored 0.9262 against a corpus of software engineering notes - the identical
score, to four decimals, that two other unrelated payloads got in a different
namespace, because all three landed on the same rank pair.

The write-time hints (``handlers/hints.py``) need the other question answered:
"is this payload actually close to that memory?" That demands a number with
magnitude, comparable between calls and independent of pool composition. Cosine
over the stored embeddings is exactly that. When embeddings are unavailable the
lexical Jaccard fallback is also absolute - a smaller instrument, but an honest
one, which a rank is not.

CALIBRATION. The cutoffs below are measured, not guessed. Positives: 228
``supersedes`` pairs from a real brain (a memory written to replace another -
the closest thing to a labelled near-duplicate). Negatives: 7,688 random
same-namespace pairs.

    measure         cutoff    random admitted    near-duplicates kept
    cosine          0.80      8.5%               84.7%
    token Jaccard   0.15      8.7%               84.2%

The two measures are set to the same operating point on purpose, so turning
embeddings off changes the instrument's precision but not the meaning of the
gate.

Do NOT port these numbers to another corpus unexamined. BGE cosine does not
bottom out near zero: two unrelated memories from one person's brain sit around
0.71 simply because they share a register (engineering session notes). A
textbook "0.9 means similar" would be meaningless here, and 0.80 would be far
too permissive somewhere else.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from . import embedding_sync
from .embeddings import EmbeddingProvider, cosine, embedding_input

logger = logging.getLogger(__name__)

# See the module docstring for how these were measured. They are the SAME
# operating point expressed in two instruments, not two independent guesses.
DEDUPE_MIN_SIMILARITY = {"cosine": 0.80, "lexical": 0.15}

# Softer than the dedupe gate because relation hits are only *candidates to
# examine*, never a claim that an edge exists. The job here is to suppress the
# floor - a store with no real neighbours should return NO suggestions rather
# than whichever three rows happened to rank highest - while still admitting
# the looser family of pairs that earn a directional edge. Both cutoffs sit
# below the p05 of real `supersedes`, `contradicts` and `caused_by` pairs.
RELATION_MIN_SIMILARITY = {"cosine": 0.72, "lexical": 0.10}

_TOKEN_RE = re.compile(r"[^\w]+", re.UNICODE)

# Tokens of 1-2 characters are almost pure noise for an overlap measure ("a",
# "of", "to", plus every stray letter from punctuation splits) and they inflate
# the union enough to distort short payloads.
_MIN_TOKEN_LEN = 3


def _tokens(title: str, content: str) -> set[str]:
    """Case-folded token set over the same text the embedder would see."""
    words = _TOKEN_RE.split(embedding_input(title, content))
    return {w.lower() for w in words if len(w) >= _MIN_TOKEN_LEN}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Intersection over union. Absolute: it depends only on the two texts."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def payload_similarity(
    conn: sqlite3.Connection,
    embedder: EmbeddingProvider | None,
    *,
    title: str,
    content: str,
    memory_ids: list[str],
) -> tuple[dict[str, float], str]:
    """Score a ``(title, content)`` payload against each of ``memory_ids``.

    Returns ``(by_id, basis)`` where ``basis`` is ``"cosine"`` or ``"lexical"``
    and names the instrument every score in ``by_id`` was produced with. The
    basis is decided ONCE per call and applies to all candidates: mixing two
    measures inside one result set would put numbers on different scales next
    to each other under one column heading, which is the class of defect this
    module exists to remove.

    On the cosine path a candidate with no vector at the active model's
    dimension is OMITTED rather than scored some other way. That is the safe
    direction: an unmeasurable hint is one we decline to show, and the row will
    be re-encoded by the existing backfill path on its next write. A missed
    hint costs a caller nothing; a confident wrong one costs a re-read.
    """
    if not memory_ids:
        return {}, "lexical"

    query_vec = None
    if embedder is not None and getattr(embedder, "enabled", False):
        try:
            query_vec = embedder.encode(embedding_input(title, content))
        except Exception:  # pragma: no cover - defensive
            logger.warning("payload encode failed; falling back to lexical", exc_info=True)

    if query_vec is not None:
        # `embedding_sync` owns reads of memory_embeddings, including the
        # mismatched-model filter. Hand-rolling the query here would make this
        # a fifth site that has to remember that rule.
        stored = embedding_sync.get_many(conn, embedder, memory_ids)
        return {mid: cosine(query_vec, vec) for mid, vec in stored.items()}, "cosine"

    placeholders = ", ".join("?" for _ in memory_ids)
    rows = conn.execute(
        f"SELECT id, title, content FROM memories WHERE id IN ({placeholders})",
        memory_ids,
    ).fetchall()
    payload = _tokens(title, content)
    return (
        {r["id"]: _jaccard(payload, _tokens(r["title"], r["content"])) for r in rows},
        "lexical",
    )
