"""``gingugu dream`` - run the consolidation pass from a shell or a cron job.

The MCP tool covers running the pass on command, inside a session. This exists
for the case the design was actually about: the pass running while nobody is
there. A scheduled run stages findings overnight, and the queue is waiting at
the next session start.

It is safe to schedule for one reason, and the reason is structural rather than
careful: the pass has no write path to ``memories``. The worst a bad run can do
is put a weak proposal in a queue that a person then declines.
"""

from __future__ import annotations

import json
import sys

from . import dream
from .config import load_config, setup_logging
from .database import Database
from .embeddings import build_provider

USAGE = """\
gingugu dream - deterministic consolidation over the memory graph

Usage:
  gingugu dream [namespace] [--json]

Runs PageRank, community detection and orphan reconnection over the relation
graph and stages what it finds in the proposal queue. Nothing is written to
memories; every finding waits for a person. Review with the `memory_dream`
tool (action="list").

Arguments:
  namespace   Limit the pass to one namespace. Omit to run over the whole store.

Options:
  --json      Emit the run report as JSON instead of a summary.
  -h, --help  Show this help and exit.
"""


def _summary(report: dict) -> str:
    graph = report["graph"]
    lines = [
        f"graph: {graph['nodes']} memories, {graph['edges']} edges, " f"{graph['orphans']} orphans",
    ]
    for name, result in report["passes"].items():
        if result.get("error"):
            lines.append(f"  {name}: FAILED (see log)")
            continue
        # "found" is what the arithmetic produced, "staged" is what reached the
        # queue. They differ when a proposal was already decided, and printing
        # both is what distinguishes a quiet run from a repeat one.
        lines.append(f"  {name}: {result['found']} found, {result['staged']} staged")
    queue = report["queue"]
    lines.append(
        f"queue: {queue['pending']} pending, {queue['accepted']} accepted, "
        f"{queue['rejected']} rejected"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "-h" in args or "--help" in args:
        print(USAGE)
        return 0

    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if len(args) > 1:
        print(f"gingugu dream: unexpected arguments {args[1:]}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    namespace = args[0] if args else None

    config = load_config()
    setup_logging(config.log_level)
    conn = Database(config.db_path).connect()

    namespace_id = None
    if namespace:
        from .namespaces import NamespaceManager

        ns = NamespaceManager(conn, config).get(namespace)
        if ns is None:
            print(f"gingugu dream: namespace {namespace!r} not found", file=sys.stderr)
            return 1
        namespace_id = ns.id

    embedder = build_provider(
        enabled=config.embeddings_enabled,
        model_name=config.embeddings_model,
        backend=config.embeddings_backend,
        ollama_host=config.embeddings_ollama_host,
        ollama_model=config.embeddings_ollama_model,
    )

    report = dream.run(conn, namespace_id=namespace_id, embedder=embedder)
    print(json.dumps(report, indent=2) if as_json else _summary(report))
    return 0
