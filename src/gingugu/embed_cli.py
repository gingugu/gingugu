"""``gingugu embed`` - finish embedding the whole store in one run.

The server backfills one small batch at startup, on purpose: a cold model
download must never block the process an editor is waiting on. That is the
right default and the wrong tool after an upgrade that changes what gets
embedded. Giving an existing brain its pieces (see ``chunking``) measured about
six minutes of CPU on 2,700 memories, which at one batch per startup is a
hundred restarts. This runs the same backfill to completion, once, on demand.

It is safe to re-run and to interrupt: every batch commits, and a memory that
already has a current vector and its pieces is never selected again.
"""

from __future__ import annotations

import sys

from .config import load_config, setup_logging
from .database import Database
from .embedding_sync import backfill
from .embeddings import build_provider

USAGE = """\
gingugu embed - encode every memory missing a vector or its pieces

Usage:
  gingugu embed [--batch-size N]

Runs the embedding backfill until nothing is left. The server does one small
batch per startup; run this once after upgrading to give every existing memory
its pieces immediately. Safe to interrupt and re-run.

Options:
  --batch-size N  Memories encoded per commit (default 64).
  -h, --help      Show this help and exit.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "-h" in args or "--help" in args:
        print(USAGE)
        return 0
    batch_size = 64
    if args[:1] == ["--batch-size"] and len(args) == 2 and args[1].isdigit() and int(args[1]):
        batch_size = int(args[1])
    elif args:
        print(f"gingugu embed: unexpected arguments {args}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    config = load_config()
    setup_logging(config.log_level)
    embedder = build_provider(
        enabled=config.embeddings_enabled,
        model_name=config.embeddings_model,
        backend=config.embeddings_backend,
        ollama_host=config.embeddings_ollama_host,
        ollama_model=config.embeddings_ollama_model,
    )
    if not getattr(embedder, "enabled", False):
        print("gingugu embed: embeddings are disabled or unavailable; nothing to do")
        return 1

    conn = Database(config.db_path).connect()
    total = 0
    try:
        while written := backfill(conn, embedder, batch_size=batch_size):
            total += written
            print(f"  {total} done", flush=True)
    finally:
        conn.close()
    print(f"gingugu embed: {total} memories embedded or pieced; nothing left to do")
    return 0
