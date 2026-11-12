"""``gingugu xray``: brief, history, blind spots, docs, env, tables, routes, surface, links, score.

``brief``, ``history``, ``blindspots``, ``docs``, ``env``, ``tables``, ``routes``,
``surface`` and ``links`` are the
same answers as the
``code_xray`` actions of those names, computed on this machine from the cached
hook graph and git log; ``score`` reads the local prediction ledger.
Exit 0 ok, 1 on a git/X-ray failure, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import json
import sys

from .xray import brief, docs_tool, env_tool, history_tool, route_tool, surface_tool, table_tool
from .xray.links import tool as links_tool
from .xray.scan import XrayError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gingugu xray", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    b = sub.add_parser("brief", help="changed files by rank and what they reach")
    b.add_argument("--range", dest="rev_range", help="git revision range, e.g. main..HEAD")
    b.add_argument("--files", help="comma-separated files instead of a range")
    b.add_argument("--path", help="any directory in the checkout (default: cwd)")
    b.add_argument("--json", action="store_true", help="the full answer as JSON")
    h = sub.add_parser("history", help="hotspots and files that change together (git log)")
    h.add_argument("--files", help="comma-separated files to focus the pairs on")
    d = sub.add_parser("blindspots", help="imported source no test reaches, by reach")
    for p in (h, d):
        p.add_argument("--path", help="any directory in the checkout (default: cwd)")
        p.add_argument("--limit", type=int, default=20, help="rows per list (default 20)")
        p.add_argument("--json", action="store_true", help="the full answer as JSON")
    c = sub.add_parser("docs", help="which docs cite a file (this repo and sibling checkouts)")
    c.add_argument("--files", help="comma-separated files to look up (default: the most cited)")
    e = sub.add_parser("env", help="env vars a file reads or sets, and where the other side is")
    e.add_argument("--files", help="comma-separated files (default: every name, one-sided first)")
    t = sub.add_parser("tables", help="DB tables a file uses, defines or drops, and the other side")
    t.add_argument("--files", help="comma-separated files (default: every table, one-sided first)")
    r = sub.add_parser("routes", help="HTTP routes a file serves or calls, and the other side")
    r.add_argument("--files", help="comma-separated files (default: every route, by its callers)")
    u = sub.add_parser("surface", help="entry points and the dangerous calls they reach")
    u.add_argument("--files", help="comma-separated files: entries in them or reaching them")
    k = sub.add_parser("links", help="which other checkouts lean on this one, and on what")
    k.add_argument("--files", help="comma-separated files: who leans on them, and the ripple")
    k.add_argument(
        "--warm", action="store_true", help="read every checkout now, filling the caches"
    )
    for p in (c, e, t, r, u, k):
        p.add_argument("--path", help="any directory in the checkout (default: cwd)")
        p.add_argument("--limit", type=int, default=20, help="rows (default 20)")
        p.add_argument("--json", action="store_true", help="the full answer as JSON")
    s = sub.add_parser("score", help="how the post-edit test predictions fared against test runs")
    s.add_argument("--json", action="store_true", help="the score as JSON")
    return parser


def _history(args: argparse.Namespace) -> int:
    try:
        files = getattr(args, "files", None)
        data = history_tool.answer(args.path, args.cmd, files, args.limit)
    except Exception as exc:  # noqa: BLE001 - a message and exit 1, never a traceback
        print(f"gingugu xray {args.cmd}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2) if args.json else data["text"])
    return 0


def _docs(args: argparse.Namespace) -> int:
    answer = (
        {
            "docs": docs_tool,
            "env": env_tool,
            "tables": table_tool,
            "routes": route_tool,
            "surface": surface_tool,
        }
        .get(args.cmd, links_tool)
        .answer
    )
    try:
        if getattr(args, "warm", False):
            data = links_tool.warm(args.path)
        else:
            data = answer(args.path, args.files, args.limit)
    except Exception as exc:  # noqa: BLE001 - a message and exit 1, never a traceback
        print(f"gingugu xray {args.cmd}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2) if args.json else data["text"])
    return 0


def _score(as_json: bool) -> int:
    from .config import load_config
    from .xray import ledger

    result = ledger.score(load_config().db_path.parent / "hook-sessions")
    print(json.dumps(result, indent=2) if as_json else ledger.render(result))
    return 0


def main(argv: list[str]) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 0 on --help, 2 on a usage error
        return exc.code if isinstance(exc.code, int) else 2
    if args.cmd == "score":
        return _score(args.json)
    if args.cmd in history_tool.ACTIONS:
        return _history(args)
    if args.cmd in ("docs", "env", "tables", "routes", "surface", "links"):
        return _docs(args)
    if args.cmd != "brief":
        parser.print_usage(sys.stderr)
        return 2
    if args.rev_range is not None and args.files:
        print("gingugu xray brief: pass --range or --files, not both", file=sys.stderr)
        return 2
    try:
        data = brief.answer(args.path, args.rev_range, args.files)
    except (XrayError, ValueError) as exc:
        print(f"gingugu xray brief: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a message and exit 1, never a traceback
        print(f"gingugu xray brief: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2) if args.json else data["text"])
    return 0
