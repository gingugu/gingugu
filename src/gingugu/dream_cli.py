"""``gingugu dream`` - run the consolidation pass from a shell or a scheduler.

The MCP tool covers running the pass on command, inside a session. This exists
for the case the design was actually about: the pass running while nobody is
there. A scheduled run stages findings while the brain is quiet, and the queue
is waiting at the next session start.

It is safe to schedule for one reason, and the reason is structural rather than
careful: the pass has no write path to ``memories``. The worst a bad run can do
is put a weak proposal in a queue that a person then declines.

``--if-idle`` is the flag that makes an OS scheduler sufficient. Point cron,
launchd or Task Scheduler at it every fifteen minutes and the command decides
for itself whether now is the time - so the recurring timer stays the operating
system's problem, and this stays a program that exits. See ``dream_schedule``.
"""

from __future__ import annotations

import json
import sys

from .config import load_config, setup_logging
from .database import Database
from .dream_schedule import SKIPPED_ACTIVE, SKIPPED_LOCKED, guarded_run
from .embeddings import build_provider

USAGE = """\
gingugu dream - deterministic consolidation over the memory graph

Usage:
  gingugu dream [namespace] [--if-idle[=MINUTES]] [--json]

Runs PageRank, community detection and orphan reconnection over the relation
graph and stages what it finds in the proposal queue. Nothing is written to
memories; every finding waits for a person. Review with the `memory_dream`
tool (action="list").

Arguments:
  namespace   Limit the pass to one namespace. Omit to run over the whole store.

Options:
  --if-idle[=MINUTES]
              Only run if the brain has gone untouched for MINUTES (default
              from MEMORY_DREAM_IDLE_MINUTES, or 20). Exits 0 without running
              if it has not. Also cancels the run between passes if activity
              resumes. This is the form to schedule.
  --json      Emit the run report as JSON instead of a summary.
  -h, --help  Show this help and exit.

Scheduling (no daemon needed - the OS timer is the daemon):
  cron      */15 * * * * gingugu dream --if-idle
  launchd   a StartInterval agent running the same command
  Windows   a Task Scheduler trigger repeating every 15 minutes
"""


def _parse_if_idle(args: list[str], default_minutes: int) -> tuple[list[str], float | None, bool]:
    """Pull ``--if-idle`` / ``--if-idle=N`` out of ``args``.

    Returns ``(remaining_args, idle_seconds, bad_value)``. ``idle_seconds`` is
    ``None`` when the flag was absent, which is what tells the rest of the
    command this is a deliberate hand-run and not a scheduled tick.
    """
    remaining: list[str] = []
    minutes: float | None = None
    for arg in args:
        if arg == "--if-idle":
            minutes = float(default_minutes)
        elif arg.startswith("--if-idle="):
            try:
                minutes = float(arg.split("=", 1)[1])
            except ValueError:
                return args, None, True
            if minutes < 0:
                return args, None, True
        else:
            remaining.append(arg)
    return remaining, (minutes * 60 if minutes is not None else None), False


def _summary(report: dict) -> str:
    if report["outcome"] == SKIPPED_ACTIVE:
        idle = report.get("idle_seconds")
        seen = f"{idle:.0f}s ago" if idle is not None else "at an unknown time"
        return f"skipped: brain last used {seen}"
    if report["outcome"] == SKIPPED_LOCKED:
        return "skipped: another dream pass is already running"

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
    if report.get("cancelled"):
        lines.append("cancelled: activity resumed; remaining passes skipped")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "-h" in args or "--help" in args:
        print(USAGE)
        return 0

    as_json = "--json" in args
    args = [a for a in args if a != "--json"]

    config = load_config()
    setup_logging(config.log_level)

    args, idle_seconds, bad_value = _parse_if_idle(args, config.dream_idle_minutes)
    if bad_value:
        print(
            "gingugu dream: --if-idle expects a non-negative number of minutes\n", file=sys.stderr
        )
        print(USAGE, file=sys.stderr)
        return 2
    if len(args) > 1:
        print(f"gingugu dream: unexpected arguments {args[1:]}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    namespace = args[0] if args else None

    conn = Database(config.db_path).connect()

    namespace_id = None
    if namespace:
        from .namespaces import NamespaceManager

        ns = NamespaceManager(conn, config).get(namespace)
        if ns is None:
            print(f"gingugu dream: namespace {namespace!r} not found", file=sys.stderr)
            return 1
        namespace_id = ns.id

    def embedder_factory():
        return build_provider(
            enabled=config.embeddings_enabled,
            model_name=config.embeddings_model,
            backend=config.embeddings_backend,
            ollama_host=config.embeddings_ollama_host,
            ollama_model=config.embeddings_ollama_model,
        )

    report = guarded_run(
        conn,
        embedder_factory=embedder_factory,
        namespace_id=namespace_id,
        idle_seconds=idle_seconds,
    )
    print(json.dumps(report, indent=2) if as_json else _summary(report))
    # A skip is a success. A scheduler that treats "the user was working" as a
    # failure will mail about it every fifteen minutes until it is muted, and a
    # muted scheduler reports nothing when the pass genuinely breaks.
    return 0
