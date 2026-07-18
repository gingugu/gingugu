"""Deterministic retrieval-quality metrics.

Every metric here is pure arithmetic over id lists — no model calls, no
randomness, no external services. This is a hard project rule, not a
style choice: benchmark grades must be reproducible math.
"""

from __future__ import annotations


def recall_at_k(relevant: list[str], retrieved: list[str], k: int) -> float:
    """Fraction of the relevant set found in the top-k retrieved results."""
    if not relevant:
        return 0.0
    hits = len(set(relevant) & set(retrieved[:k]))
    return hits / len(relevant)


def precision_at_k(relevant: list[str], retrieved: list[str], k: int) -> float:
    """Fraction of the top-k retrieved results that are relevant.

    Divides by ``k`` (canonical definition), so returning fewer than k
    results is penalized rather than hidden.
    """
    if k <= 0:
        return 0.0
    hits = len(set(relevant) & set(retrieved[:k]))
    return hits / k


def mrr(relevant: list[str], retrieved: list[str]) -> float:
    """Reciprocal rank of the first relevant result (0.0 if none retrieved)."""
    relevant_set = set(relevant)
    for i, mid in enumerate(retrieved, start=1):
        if mid in relevant_set:
            return 1.0 / i
    return 0.0


def estimate_tokens(texts: list[str]) -> int:
    """Approximate token cost of returning these texts to an agent.

    Uses the ~4 chars/token heuristic. Deliberately tokenizer-free: the
    number only needs to be comparable across runs of the same corpus,
    not exact.
    """
    return sum(len(t) for t in texts) // 4


def mean(values: list[float]) -> float:
    """Arithmetic mean, 0.0 for an empty list."""
    return sum(values) / len(values) if values else 0.0
