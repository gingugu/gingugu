"""The dream pass - deterministic consolidation that never touches the brain.

Sleep does not store new experience; it replays what is already there and
extracts the structure across it. This package is the closest a program of this
shape can honestly get: a pass that runs while nobody is watching, computes
over the memories already written, and reports what the shape of them turned
out to be.

**Everything here is arithmetic, and that is a constraint rather than an
implementation detail.** No model forms an opinion at any point. PageRank,
label propagation and cosine similarity are the whole of it - three published
algorithms whose output a person can recompute by hand and check. The rule the
package exists to honour is that nothing decides what is in the brain except
the person whose brain it is.

The line those three algorithms stop at is the same line every time:

    math finds STRUCTURE, and structure is not CONTENT.

Centrality finds which memories the graph leans on; whether "central" means
"identity" is a person's call. Clustering finds which memories group; naming
the group is prose. Similarity finds which two memories belong connected;
typing the edge between them is a claim about what happened. In all three the
computation hands over a shape and stops.

So nothing here writes to ``memories``, and nothing here writes a relation.
Findings are staged in the ``proposals`` queue and wait. That is what makes the
pass safe to put on a cron: the worst a bad run can do is waste a reader's
time.

**What is deliberately not built yet.** Co-access (memories retrieved together
are associated - Hebbian, and pure counting) is the fourth pass and the
design's original motivation. It needs session-tagged reads to count, which
``access_log`` only began recording recently, so the data is thin: measured
today, a few hundred rows across a couple of dozen sessions. The three passes
here are fully computable on the graph as it already stands, so they ship first
and the co-access evidence accumulates behind them.
"""

from __future__ import annotations

import logging
import sqlite3

from ..embeddings import EmbeddingProvider
from ..proposals import ProposalQueue, ordered_pair
from . import centrality, clusters, orphans
from . import graph as graph_mod

logger = logging.getLogger(__name__)

# Pass name -> the kind of proposal it stages. The pass name is recorded on
# every row so a reviewer can tell which computation produced a finding, and so
# the governance work can later accumulate precedent per class rather than in
# one undifferentiated pile - approvals of orphan reconnections should buy
# nothing for centrality claims.
PASS_KINDS = {
    "centrality": "core",
    "clusters": "cluster",
    "orphans": "edge",
}


def run(
    conn: sqlite3.Connection,
    *,
    namespace_id: str | None = None,
    embedder: EmbeddingProvider | None = None,
) -> dict:
    """Run every pass and stage what they found. Returns a report.

    The graph is loaded once and shared, so the three passes are guaranteed to
    be describing the same thing. A pass that raises is logged and skipped
    rather than allowed to abort the run: this is scheduled work with no one
    watching, and losing two good passes because a third hit an edge case would
    be the worst possible trade.
    """
    graph = graph_mod.load(conn, namespace_id=namespace_id)
    queue = ProposalQueue(conn)

    report: dict = {
        "graph": {
            "nodes": len(graph.nodes),
            "edges": graph.edge_count,
            "orphans": len(graph.orphans),
        },
        "passes": {},
    }

    for name, finder in (
        ("centrality", lambda: centrality.find(conn, graph)),
        ("clusters", lambda: clusters.find(graph)),
        ("orphans", lambda: orphans.find(conn, graph, embedder=embedder)),
    ):
        try:
            findings = finder()
        except Exception:
            logger.exception("dream pass %r failed; continuing", name)
            report["passes"][name] = {"found": 0, "staged": 0, "error": True}
            continue
        report["passes"][name] = _stage_all(queue, graph, name, findings)

    report["queue"] = queue.counts()
    return report


def _stage_all(queue: ProposalQueue, graph, pass_name: str, findings: list[dict]) -> dict:
    """Write one pass's findings to the queue.

    ``staged`` counts rows actually written; the difference from ``found`` is
    the proposals a person has already ruled on, which the queue refuses to
    raise a second time. Reporting both is what tells a reader whether a quiet
    run means "nothing new" or "everything new was already rejected".
    """
    kind = PASS_KINDS[pass_name]
    staged = 0
    for finding in findings:
        subject = finding["subject_id"]
        obj = finding.get("object_id")
        if obj is not None:
            # Closeness has no direction, so the pair is stored in a fixed
            # order and the same finding cannot be staged twice, once per side.
            subject, obj = ordered_pair(subject, obj)
        try:
            written = queue.stage(
                pass_name=pass_name,
                kind=kind,
                subject_id=subject,
                object_id=obj,
                score=finding["score"],
                evidence=finding["evidence"],
                namespace_id=graph.namespace_id_of.get(subject),
            )
        except (ValueError, sqlite3.Error):
            logger.warning("could not stage %s proposal for %s", kind, subject, exc_info=True)
            continue
        staged += int(written)

    return {"found": len(findings), "staged": staged}
