"""PageRank over the relation graph - a computed answer to "what is core?".

The identity tier is chosen by hand today: a memory gets pinned because someone
decided it mattered. That works, and it has a blind spot - it can only ever
contain what somebody thought to nominate. Centrality asks the graph instead.
A memory that many well-connected memories point at is load-bearing whether or
not anyone noticed, and that is a measurement, not an opinion.

What the pass will not do is act on it. Being structurally central and
belonging in the identity tier are different claims: the second is about what
*matters*, and no amount of connectivity settles it. So the finding is staged
as a ``core`` proposal carrying its rank, and a person decides.

**Why PageRank rather than raw degree.** Degree counts neighbours and stops.
PageRank counts neighbours weighted by *their* standing, which is the
difference between a memory linked by ten trivia notes and one linked by three
memories that the rest of the graph leans on. The second is the one worth
surfacing, and degree cannot tell them apart.
"""

from __future__ import annotations

import sqlite3

from .graph import Graph

# Standard PageRank damping. 0.85 is the value the algorithm was published with
# and the one every implementation uses; there is nothing to tune here and
# inventing a different number would only make our ranks incomparable with
# every published description of what they mean.
DAMPING = 0.85

# Iteration bounds. The loop exits on convergence; the cap is a guarantee that
# an unusual graph cannot hang a cron job.
MAX_ITERATIONS = 100
TOLERANCE = 1e-9

# How far above flat a memory must sit before it is worth a human's attention.
# If every memory had the same rank, each would hold exactly ``1/N``. Three
# times that is the floor for calling something central rather than ordinary -
# a deliberately blunt cutoff, chosen because the alternative is a tuned
# threshold that would encode a preference about what deserves the identity
# tier, which is the judgment this pass is not allowed to make.
BASELINE_MULTIPLE = 3.0

# The queue is meant to be worked, not admired. A run stages the strongest
# handful rather than every memory clearing the floor.
TOP_N = 10


def pagerank(graph: Graph) -> dict[str, float]:
    """Undirected PageRank. Deterministic for a given graph.

    Rank leaked by dangling nodes (orphans, which have no neighbours to pass it
    to) is redistributed uniformly rather than discarded, so the vector stays a
    probability distribution and the ``1/N`` baseline the threshold is defined
    against keeps meaning what it says.
    """
    nodes = graph.nodes
    n = len(nodes)
    if n == 0:
        return {}

    uniform = 1.0 / n
    rank = dict.fromkeys(nodes, uniform)

    for _ in range(MAX_ITERATIONS):
        leaked = sum(rank[node] for node in nodes if not graph.adjacency.get(node))
        base = (1.0 - DAMPING) * uniform + DAMPING * leaked * uniform

        nxt = {}
        for node in nodes:
            inflow = sum(
                rank[nbr] / len(graph.adjacency[nbr]) for nbr in graph.adjacency.get(node, ())
            )
            nxt[node] = base + DAMPING * inflow

        delta = sum(abs(nxt[node] - rank[node]) for node in nodes)
        rank = nxt
        if delta < TOLERANCE:
            break

    return rank


def _pinned_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT id FROM memories WHERE pinned = 1").fetchall()
    return {row["id"] for row in rows}


def find(conn: sqlite3.Connection, graph: Graph) -> list[dict]:
    """Central memories worth proposing for the identity tier.

    Already-pinned memories are skipped. They are the expected result - the
    hand-picked tier and the computed one *should* overlap heavily - and
    proposing what is already true would bury the actual finding, which is the
    memory the graph ranks alongside them that nobody nominated.
    """
    if not graph.nodes:
        return []

    ranks = pagerank(graph)
    floor = BASELINE_MULTIPLE / len(graph.nodes)
    pinned = _pinned_ids(conn)
    top = max(ranks.values()) if ranks else 0.0

    candidates = [
        (node, rank) for node, rank in ranks.items() if rank >= floor and node not in pinned
    ]
    # Rank descending, id ascending: two memories on an identical rank must come
    # back in the same order on every run or the "top N" slice is a coin flip.
    candidates.sort(key=lambda item: (-item[1], item[0]))

    findings = []
    for position, (node, rank) in enumerate(candidates[:TOP_N], start=1):
        findings.append(
            {
                "subject_id": node,
                # Normalized against the run's own top rank so the queue can sort
                # a centrality finding next to a similarity one without putting
                # two different scales under one column. The raw number is kept
                # in the evidence, where its actual units survive.
                "score": round(rank / top, 4) if top else 0.0,
                "evidence": {
                    "pagerank": round(rank, 8),
                    "baseline": round(1.0 / len(graph.nodes), 8),
                    "times_baseline": round(rank * len(graph.nodes), 2),
                    "degree": graph.degree(node),
                    "rank_position": position,
                    "graph_nodes": len(graph.nodes),
                    "graph_edges": graph.edge_count,
                },
            }
        )
    return findings
