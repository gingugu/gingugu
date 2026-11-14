"""``gingugu promote``: the command line over ``promote`` and ``promote_crew``.

Two forms:

    gingugu promote --to-crew --source-ns NS [--skip-tag T ...] [--dry-run]
        A crew mate's gold, from this machine's local brain to its crew link,
        as its persona (MEMORY_PERSONA). Run --dry-run first and read the list.

    gingugu promote --source-url URL --target-url URL --source-ns NS --target-ns NS
        Brain to brain over HTTP; tokens from GINGUGU_SOURCE_TOKEN and
        GINGUGU_TARGET_TOKEN so they never land in argv or shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gingugu promote", description=__doc__)
    p.add_argument("--to-crew", action="store_true", help="Promote to this machine's crew")
    p.add_argument("--source-ns", required=True, help="Source namespace to read from")
    p.add_argument("--skip-tag", action="append", default=[], help="Hold back this tag too")
    p.add_argument("--source-url", help="Source MCP URL (e.g. https://host:8765/mcp)")
    p.add_argument("--target-url", help="Central MCP URL")
    p.add_argument("--target-ns", help="Central namespace to write to")
    p.add_argument("--contributor", default=os.environ.get("USER", "unknown"))
    p.add_argument("--instance", default="local", help="Label for this source instance")
    p.add_argument(
        "--dry-run", action="store_true", help="Report what would promote, write nothing"
    )
    return p


def _to_crew(args: argparse.Namespace) -> dict:
    from . import crew, remote
    from .config import load_config
    from .promote_crew import promote_to_crew
    from .prompt_hook import persona

    me = persona()
    if not me:
        raise ValueError("set MEMORY_PERSONA to your name first - promotions go up as you")
    if remote.active() is not None:
        raise ValueError("this machine's brain is remote already; --to-crew reads a local brain")
    target = crew.active()
    if target is None:
        raise ValueError("no crew link; run `gingugu crew join` first")
    token = crew.token(target)
    if not token:
        raise ValueError(f"no token for {target.url} in the keychain; rerun `gingugu crew join`")
    return asyncio.run(
        promote_to_crew(
            db_path=load_config().db_path,
            source_namespace=args.source_ns,
            target=target,
            token=token,
            persona=me,
            skip_tags=args.skip_tag,
            dry_run=args.dry_run,
        )
    )


def _url_to_url(args: argparse.Namespace) -> dict:
    from .promote import run_promotion

    if not (args.source_url and args.target_url and args.target_ns):
        raise ValueError(
            "--source-url, --target-url and --target-ns are required without --to-crew"
        )
    return asyncio.run(
        run_promotion(
            source_url=args.source_url,
            source_token=os.environ.get("GINGUGU_SOURCE_TOKEN", ""),
            source_namespace=args.source_ns,
            target_url=args.target_url,
            target_token=os.environ.get("GINGUGU_TARGET_TOKEN", ""),
            target_namespace=args.target_ns,
            contributor=args.contributor,
            instance=args.instance,
            dry_run=args.dry_run,
        )
    )


def main(argv: list[str] | None = None) -> int:
    """Console entry point; returns the exit code."""
    args = _parser().parse_args(argv)
    try:
        report = _to_crew(args) if args.to_crew else _url_to_url(args)
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"gingugu promote: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0
