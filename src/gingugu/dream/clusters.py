"""Community detection - the groups the graph forms without being told to.

Tags and namespaces record the structure somebody *authored*. This finds the
structure that emerged: sets of memories that link to each other far more than
to anything outside the set. Those groups exist whether or not a tag was ever
invented for them, and a group with no name is exactly the kind of thing a
person forgets they know.

The pass proposes membership and nothing else. **Naming a cluster is prose**,
and prose is the line this package does not cross - "these eleven memories
belong together" is countable, "these eleven memories are about release
discipline" is a judgment about meaning.

**Label propagation, not Louvain.** Louvain optimises modularity and would
likely find slightly better communities; it also needs a resolution parameter,
and picking one is choosing how coarse the answer should be - a preference
dressed as a setting. Label propagation has no such knob. It converges to
whatever the edges already say, which is the only thing this pass is entitled
to report.
"""

from __future__ import annotations

from collections import Counter

from .graph import Graph

# Propagation is a fixpoint iteration; it normally settles in well under ten
# passes. The cap exists so an oscillating graph cannot hang a cron job.
MAX_ROUNDS = 50

# Two memories with an edge between them are a relation, not a community.
MIN_CLUSTER_SIZE = 3

# A proposal a person cannot act on is not a proposal. Past a few dozen members
# "these belong together" stops being a finding and starts being a description
# of the namespace, so oversized components are counted and left alone rather
# than staged as work nobody can review.
MAX_CLUSTER_SIZE = 40

# More of the group's edges must stay inside it than leave it. Below half, the
# label survived the sweep by accident rather than because the set is
# separable, and reporting it as a community would be claiming something the
# edges do not support. Not a tuned threshold - the point where the majority
# flips is the only cutoff here that means anything on its own.
MIN_DENSITY = 0.5

# Findings staged per run. Measured on a real 1,891-memory brain, the graph
# decomposes into 186 communities clearing the floor - a queue at that depth is
# one nobody works through, which is the same failure as an unreadable
# proposal, at a different scale. The strongest are staged, the rest arrive on
# later runs as those are decided. Matches how the other two passes bound
# themselves.
TOP_N = 15


def propagate(graph: Graph) -> dict[str, str]:
    """Assign every node a community label. Deterministic.

    Each node adopts the label most common among its neighbours, ties broken by
    the lexicographically smallest label. Updates are applied in place as the
    sweep proceeds, which converges faster than a synchronous update and, more
    importantly, cannot oscillate between two states forever the way the
    synchronous form does on a bipartite graph.

    Both the sweep order and the tie-break are what make this reproducible.
    Published label propagation randomises node order on purpose, to sample
    different local optima; we take the opposite trade deliberately. A pass
    that reports different communities on each run of an unchanged graph would
    be unauditable, and the queue's whole claim is that a person can check the
    arithmetic.
    """
    labels = {node: node for node in graph.nodes}

    for _ in range(MAX_ROUNDS):
        changed = False
        for node in graph.nodes:
            neighbours = graph.adjacency.get(node)
            if not neighbours:
                continue
            counts = Counter(labels[nbr] for nbr in neighbours)
            best_count = max(counts.values())
            best = min(label for label, count in counts.items() if count == best_count)
            if best != labels[node]:
                labels[node] = best
                changed = True
        if not changed:
            break

    return labels


def _boundary(graph: Graph, members: set[str]) -> tuple[int, int]:
    """Count (internal, boundary) edge endpoints for a member set."""
    internal = boundary = 0
    for node in members:
        for nbr in graph.adjacency.get(node, ()):
            if nbr in members:
                internal += 1
            else:
                boundary += 1
    return internal // 2, boundary


def find(graph: Graph) -> list[dict]:
    """Communities worth a person's attention, best-separated first."""
    if not graph.nodes:
        return []

    labels = propagate(graph)
    groups: dict[str, list[str]] = {}
    for node in graph.nodes:  # graph order, so member lists are stable
        groups.setdefault(labels[node], []).append(node)

    findings = []
    for members in groups.values():
        if not MIN_CLUSTER_SIZE <= len(members) <= MAX_CLUSTER_SIZE:
            continue
        member_set = set(members)
        internal, boundary = _boundary(graph, member_set)

        # Density: the share of the community's edges that stay inside it. 1.0
        # is a component with no outside links at all; a value near zero means
        # the label survived by accident rather than because the group is
        # separable. This is the standard conductance complement, so a reader
        # who knows the measure needs no explanation from us.
        density = internal / (internal + boundary) if (internal + boundary) else 0.0
        if density < MIN_DENSITY:
            continue

        # The best-connected member, as the row the queue hangs the group off.
        # It is a handle, not a claim that this memory leads the cluster.
        anchor = max(members, key=lambda node: (graph.degree(node), node))

        findings.append(
            {
                "subject_id": anchor,
                "score": round(density, 4),
                "evidence": {
                    "size": len(members),
                    "members": members,
                    "internal_edges": internal,
                    "boundary_edges": boundary,
                    "density": round(density, 4),
                    "anchor_degree": graph.degree(anchor),
                    "namespaces": sorted({graph.namespace_of.get(m, "") for m in members}),
                },
            }
        )

    # Density first, then the larger group, then id - the last is what keeps a
    # run of identical scores in the same order on every run, which is what
    # makes "the top fifteen" a fact rather than a coin flip.
    findings.sort(key=lambda f: (-f["score"], -f["evidence"]["size"], f["subject_id"]))
    return findings[:TOP_N]
