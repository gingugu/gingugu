"""Dump a Gingugu DB as the Explorer's static-mode data (the GitHub Pages demo).

Writes three files under ``ui/src/data/static/`` by running the real read API
(``gingugu.ui_api.dispatch``) against the DB you name, so the static demo is
exactly what a live brain would have answered:

* ``graph.json``   - the ``/graph`` payload
* ``info.json``    - the ``/info`` payload plus ``mode``/``brain``/``persona``
* ``details.json`` - ``{memory id: /memory/<id> payload}`` for every node

The output is committed and published, so ``--db`` is required: point it at a
fictional demo DB, never at a real brain.

    uv run python ui/dump_static.py --db /path/to/demo.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from gingugu.ui_api import UiEnv, dispatch

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
logger = logging.getLogger(__name__)

DEFAULT_OUT = Path(__file__).resolve().parent / "src" / "data" / "static"


def _get(env: UiEnv, path: str) -> dict:
    status, body = dispatch(path, {}, env)
    if status != 200:
        raise SystemExit(f"{path} answered {status}: {body}")
    return body


def build(db_path: Path, persona: str | None = None) -> dict[str, dict]:
    env = UiEnv(db_path=db_path, embedder=lambda: None)
    graph = _get(env, "/graph")
    info = {**_get(env, "/info"), "mode": "static", "brain": "demo", "persona": persona}
    details = {n["id"]: _get(env, f"/memory/{n['id']}") for n in graph["nodes"]}
    return {"graph": graph, "info": info, "details": details}


def write(db_path: Path, out: Path, *, persona: str | None = None) -> None:
    payloads = build(db_path, persona)
    out.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        (out / f"{name}.json").write_text(text + "\n")
    logger.info("Wrote %d memories to %s", len(payloads["details"]), out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump a Gingugu DB for the UI's static mode.")
    parser.add_argument("--db", type=Path, required=True, help="SQLite DB to dump (use a demo DB).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"Output dir ({DEFAULT_OUT})")
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit(f"no such DB: {args.db}")

    write(args.db, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
