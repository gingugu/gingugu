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

import math
import re
from collections import Counter

from .graph import Graph

# Tags that record WHEN rather than WHAT: an ISO date, or a sail ordinal. Every
# memory saved in one sitting carries the same ones, so a cohesion measure that
# counted them would score a session 1.0 and rediscover exactly the structure
# this ranking exists to see past. Measured on the live brain: the four
# highest-cohesion groups under a naive count were all single sessions held
# together by a date tag alone.
_TIME_SHAPED = re.compile(
    r"""^(
        \d{4}[-_]\d{2}[-_]\d{2}   # 2026-08-21, 2026_07_25
        | \d{1,3}(st|nd|rd|th)-sail  # 24th-sail
        | \d{4}                   # a bare year
    )$""",
    re.VERBOSE,
)

# A tag has to be on most of the group before "the group is about this" is a
# statement about the group rather than about a subset of it.
MIN_TAG_COHESION = 0.5

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


def _tag_cohesion(graph: Graph, members: list[str]) -> dict:
    """How much of this group shares a name that MEANS something, and who lacks it.

    Coverage alone does not work, and that is measured rather than assumed.
    Scored against fifteen hand-decided clusters, ranking by plain coverage
    separated accepts from rejects with AUC 0.56 - chance is 0.50. The reason is
    that any store grows a handful of tags carried by a large share of its
    memories. Those blanket every group drawn from that part of the store while
    saying nothing about any of them, exactly as a date tag does.

    Weighting coverage by inverse document frequency fixes what the filter
    cannot. A tag on six memories that covers five of this group is a finding; a
    tag on a hundred that covers all six is a coincidence. Same fifteen
    clusters, AUC 0.68.

    Three numbers come back, and the third decides whether the proposal can do
    anything at all:

    * ``tag_score`` - coverage x IDF for the strongest tag. The ranking key.
    * ``tag_cohesion`` - plain coverage of that tag, kept because it is the one
      a reader can verify by counting.
    * ``tag_gap`` - how many members lack it. **Zero means the group is already
      fully labelled**, so accepting could apply nothing.

    Counting only. Which tag is strongest is arithmetic; whether it is the right
    NAME for the group stays with the reader.
    """
    counts: Counter[str] = Counter()
    for member in members:
        for tag in graph.tags_of.get(member, ()):
            if not _TIME_SHAPED.match(tag):
                counts[tag] += 1

    size = len(members)
    if not counts:
        return {
            "shared_tags": {},
            "dominant_tag": None,
            "tag_score": 0.0,
            "tag_cohesion": 0.0,
            "tag_gap": size,
        }

    corpus = max(len(graph.nodes), 1)

    def weighted(tag: str, count: int) -> float:
        frequency = max(graph.tag_frequency.get(tag, 1), 1)
        return (count / size) * math.log(corpus / frequency)

    # Ties broken lexicographically, so a run is reproducible.
    best = max(counts.items(), key=lambda kv: (weighted(*kv), -counts[kv[0]], kv[0]))
    dominant, dominant_count = best

    return {
        # Only the tags that actually span the group; the long tail of
        # one-member tags is noise in a report a person has to read.
        "shared_tags": {
            tag: count
            for tag, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            if count / size >= MIN_TAG_COHESION
        },
        "dominant_tag": dominant,
        "tag_score": round(weighted(dominant, dominant_count), 4),
        "tag_cohesion": round(dominant_count / size, 4),
        "tag_gap": size - dominant_count,
    }


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

        cohesion = _tag_cohesion(graph, members)

        # Nothing to apply, so nothing to review. A group whose dominant tag is
        # already on every member cannot be improved by accepting it, and
        # staging it spends a reviewer's attention to reach "no change".
        if cohesion["dominant_tag"] is not None and cohesion["tag_gap"] == 0:
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
                    **cohesion,
                },
            }
        )

    # Tag score leads and density follows, rather than the other way round.
    # Density turns out not to discriminate at all: on a real brain every
    # community clearing the floor is a fully separated component scoring
    # exactly 1.0, so ranking by it left fifteen findings tied and the order
    # decided by id - arbitrary, dressed as "strongest first".
    #
    # Honest about the strength of what replaces it: AUC 0.68 against fifteen
    # hand-decided clusters. Better than the arbitrary order it replaces, and
    # NOT strong enough to treat as settled. Re-measure once the decision log
    # passes ~50 clusters, and note that some rejects are unreachable by any
    # tag arithmetic - one was rejected because the dominant tag would have
    # been FALSE of a single member, which no count can see.
    findings.sort(
        key=lambda f: (
            -f["evidence"]["tag_score"],
            -f["score"],
            -f["evidence"]["size"],
            f["subject_id"],
        )
    )
    return findings[:TOP_N]
