"""Dump the live Gingugu DB as JSON for the UI's static mode.

Writes ``ui/src/data/sample.json`` so the React app has fresh data to render
without running the API server (also used by the GitHub Pages build).

Run from the repo root:
    uv run python ui/dump_static.py
    uv run python ui/dump_static.py --out ui/src/data/sample.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from gingugu.config import load_config
from gingugu.database import Database
from gingugu.portability import export_data

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
logger = logging.getLogger(__name__)

DEFAULT_OUT = Path(__file__).resolve().parent / "src" / "data" / "sample.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump Gingugu DB to a static JSON file for the UI.")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output path (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args()

    config = load_config()
    db = Database(config.db_path)
    conn = db.connect()
    payload = export_data(conn)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2))

    logger.info(
        "Wrote %d memories across %d namespaces to %s",
        len(payload.get("memories", [])),
        len(payload.get("namespaces", [])),
        args.out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
