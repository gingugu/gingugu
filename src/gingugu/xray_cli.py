"""``gingugu xray brief``: the X-ray brief from a shell, for agents and humans.

Same answer as ``code_xray`` action ``brief``, computed on this machine from the
cached hook graph. Exit 0 ok, 1 on a git/X-ray failure, 2 on bad usage.
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
    return parser


def main(argv: list[str]) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 0 on --help, 2 on a usage error
        return exc.code if isinstance(exc.code, int) else 2
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
    print(json.dumps(data, indent=2) if args.json else data["text"])
    return 0
