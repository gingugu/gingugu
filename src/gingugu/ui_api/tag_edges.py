"""Tag edges for the Explorer graph: who shares a meaningful tag with whom.

The weight of a pair is the sum, over the tags they share, of that tag's
inverse document frequency, ``ln(N / df)``. A tag on two memories says a lot
about those two; a tag on half the brain says almost nothing. Plain math over
the tag table - no model decides what is linked.

Three things keep this from wiring the whole brain into one hairball:

* **Top-k per node.** Each memory keeps its ``top_k`` strongest pairs; the edge
  set is the union of every node's picks, so a node can end up with more than
  ``top_k`` edges (others picked it) but never fewer than it could have had.
* **max_df.** A tag on more than ``max_df`` memories (``mr-boomtastic``,
  ``reflection``) contributes no pairs at all. Its IDF is near zero anyway, and
  skipping it is what keeps the pair count bounded by ``max_df * N`` instead of
  ``N^2``.
* **Date tags are opt-in.** ``2026-10-20`` is rare, so IDF rates it highly, but
  "written the same day" is a time ribbon, not a topic. ``include_dates`` keeps
  them for anyone who wants the ribbons back.
"""

from __future__ import annotations

import heapq
import math
import re
from collections import defaultdict

DEFAULT_TOP_K = 4
DEFAULT_MAX_DF = 200

_DATE_TAG = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def is_date_tag(tag: str) -> bool:
    """True for ``YYYY-MM`` and ``YYYY-MM-DD`` stamps."""
    return bool(_DATE_TAG.match(tag))


def _postings(tags_by_node: dict[str, list[str]], include_dates: bool) -> dict[str, list[str]]:
    postings: dict[str, set[str]] = defaultdict(set)
    for node, names in tags_by_node.items():
        for name in names:
            if include_dates or not is_date_tag(name):
                postings[name].add(node)
    return {tag: sorted(nodes) for tag, nodes in postings.items()}


def tag_edges(
    tags_by_node: dict[str, list[str]],
    *,
    top_k: int = DEFAULT_TOP_K,
    max_df: int = DEFAULT_MAX_DF,
    include_dates: bool = False,
) -> list[tuple[str, str, float]]:
    """``(a, b, weight)`` with ``a < b``, strongest first, ties by id."""
    n = len(tags_by_node)
    if n < 2 or top_k < 1:
        return []
    pair_weight: dict[tuple[str, str], float] = defaultdict(float)
    for nodes in _postings(tags_by_node, include_dates).values():
        df = len(nodes)
        if df < 2 or df > max_df:
            continue
        idf = math.log(n / df)
        for i, a in enumerate(nodes):
            for b in nodes[i + 1 :]:
                pair_weight[(a, b)] += idf

    neighbours: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (a, b), w in pair_weight.items():
        neighbours[a].append((w, b))
        neighbours[b].append((w, a))

    chosen: set[tuple[str, str]] = set()
    for node, cands in neighbours.items():
        # Highest weight first; equal weights resolve by neighbour id so the
        # result never depends on dict order.
        for _, other in heapq.nsmallest(top_k, cands, key=lambda c: (-c[0], c[1])):
            chosen.add((node, other) if node < other else (other, node))

    edges = [(a, b, round(pair_weight[(a, b)], 3)) for a, b in chosen]
    edges.sort(key=lambda e: (-e[2], e[0], e[1]))
    return edges
