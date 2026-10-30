"""``gingugu xray``: the X-ray brief and the test-prediction score, from a shell.

``brief`` is the same answer as ``code_xray`` action ``brief``, computed on this
machine from the cached hook graph; ``score`` reads the local prediction ledger.
Exit 0 ok, 1 on a git/X-ray failure, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import json
import sys

from .xray import brief
from .xray.scan import XrayError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gingugu xray", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    b = sub.add_parser("brief", help="changed files by rank and what they reach")
    b.add_argument("--range", dest="rev_range", help="git revision range, e.g. main..HEAD")
    b.add_argument("--files", help="comma-separated files instead of a range")
    b.add_argument("--path", help="any directory in the checkout (default: cwd)")
    b.add_argument("--json", action="store_true", help="the full answer as JSON")
    s = sub.add_parser("score", help="how the post-edit test predictions fared against pytest")
    s.add_argument("--json", action="store_true", help="the score as JSON")
    return parser


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
