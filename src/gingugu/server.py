"""MCP server entry point.

Builds the FastMCP server over stdio, wires up storage/namespace dependencies,
registers tool handlers, and runs. stdout is the MCP transport — all logging
goes to stderr (see config.setup_logging).
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .database import Database
from .embeddings import build_provider
from .handlers import ServerContext, register_all
from .namespaces import NamespaceManager
from .storage import MemoryStore

logger = logging.getLogger(__name__)


def build_server() -> FastMCP:
    """Construct and fully wire the FastMCP server."""
    config = load_config()
    from .config import setup_logging

    setup_logging(config.log_level)
    logger.info("Starting Gingugu (namespace=%s)", config.resolved_namespace)

    db = Database(config.db_path)
    conn = db.connect()

    embedder = build_provider(
        enabled=config.embeddings_enabled,
        model_name=config.embeddings_model,
        backend=config.embeddings_backend,
        ollama_host=config.embeddings_ollama_host,
        ollama_model=config.embeddings_ollama_model,
    )
    store = MemoryStore(conn, embedder=embedder)

    # Backfill missing embeddings (small batch — lazy, so first store/recall
    # absorbs the model download cost rather than blocking startup forever
    # on large stores). Subsequent recalls will surface the rest naturally
    # as memories get embedded on write.
    try:
        if embedder.enabled:
            store.backfill_embeddings(batch_size=32)
    except Exception:  # pragma: no cover - defensive
        logger.exception("startup embedding backfill failed; continuing without")

    ctx = ServerContext(
        config=config,
        store=store,
        namespaces=NamespaceManager(conn, config),
        conn=conn,
    )

    mcp = FastMCP("gingugu")
    register_all(mcp, ctx)
    return mcp


USAGE = """\
gingugu — persistent long-term memory for AI coding assistants (MCP server)

Usage:
  gingugu                      Run the MCP server over stdio (default transport
                               for local clients like Claude Code / Cursor).
  gingugu serve                Run over streamable HTTP for a remote/central brain.
  gingugu promote [options]    Promote local gold memories up to a central brain.
  gingugu init [options]       Bootstrap a repo so an AI assistant uses Gingugu.

Options:
  -h, --help                   Show this help and exit.
  -V, --version                Show the version and exit.

Run a subcommand with --help for its own options, e.g. `gingugu init --help`.
The active namespace is set via the MEMORY_NAMESPACE environment variable.
"""


def main() -> None:
    """Console-script entry point.

    ``gingugu``         → run over stdio (default; local MCP client transport).
    ``gingugu serve``   → run over streamable HTTP for a remote/central brain.
    ``gingugu promote`` → promote local gold memories up to a central brain.
    ``gingugu init``    → bootstrap a repo's Claude Code hooks / rules file.
    """
    import sys

    from . import __version__

    cmd = sys.argv[1:2]

    if cmd in (["-h"], ["--help"], ["help"]):
        print(USAGE)
        return
    if cmd in (["-V"], ["--version"], ["version"]):
        print(f"gingugu {__version__}")
        return
    if cmd == ["serve"]:
        from .serve import serve

        serve()
        return
    if cmd == ["promote"]:
        from .promote import main as promote_main

        promote_main(sys.argv[2:])
        return
    if cmd == ["init"]:
        from .bootstrap import main as init_main

        raise SystemExit(init_main(sys.argv[2:]))
    # Bare `gingugu` → stdio server (the MCP client transport; takes no CLI
    # args — namespace comes from the environment). Any leftover token is a
    # typo, not an MCP handshake — fail loudly instead of silently blocking
    # on stdin.
    if cmd:
        print(f"gingugu: unknown command '{cmd[0]}'\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    build_server().run()


if __name__ == "__main__":
    main()
