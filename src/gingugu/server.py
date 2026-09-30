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


def build_server(transport: str = "stdio") -> FastMCP:
    """Construct and fully wire the FastMCP server.

    ``transport`` is "stdio" or "http"; `gingugu serve` passes "http".
    """
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
        transport=transport,
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
  gingugu ui [options]         Launch the Memory Explorer web UI in a browser.
  gingugu dream [namespace]    Run the deterministic consolidation pass over the
                               memory graph and stage what it finds for review.
                               Writes only to the proposal queue, never to
                               memories - safe to put on a cron.
  gingugu embed                Finish embedding the whole store in one run (the
                               server does one small batch per startup).
  gingugu hook prompt          Involuntary recall: reads a UserPromptSubmit
                               event on stdin and surfaces memories the prompt
                               woke. Wired by `gingugu init`; not run by hand.
  gingugu hook tool            Tripwires: reads a PreToolUse event on stdin and
                               stops a call that matches a memory's tripwire.
                               Wired by `gingugu init`; not run by hand.

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
    ``gingugu ui``      → launch the Memory Explorer web UI in a browser.
    ``gingugu dream``   → run the consolidation pass; stages proposals only.
    ``gingugu embed``   → run the embedding backfill to completion.
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
        from .serve import SERVE_USAGE, serve

        rest = sys.argv[2:]
        if rest and rest[0] in ("-h", "--help"):
            print(SERVE_USAGE)
            return
        if rest:
            print(f"gingugu serve: unexpected argument {rest[0]!r}\n", file=sys.stderr)
            print(SERVE_USAGE, file=sys.stderr)
            raise SystemExit(2)
        serve()
        return
    if cmd == ["promote"]:
        from .promote import main as promote_main

        promote_main(sys.argv[2:])
        return
    if cmd == ["init"]:
        from .bootstrap import main as init_main

        raise SystemExit(init_main(sys.argv[2:]))
    if cmd == ["ui"]:
        from .webui import main as ui_main

        raise SystemExit(ui_main(sys.argv[2:]))
    if cmd == ["dream"]:
        from .dream_cli import main as dream_main

        raise SystemExit(dream_main(sys.argv[2:]))
    if cmd == ["embed"]:
        from .embed_cli import main as embed_main

        raise SystemExit(embed_main(sys.argv[2:]))
    if cmd == ["hook"]:
        # Runs on the user's keystroke via a Claude Code hook. Reads the event
        # payload on stdin; never raises, never blocks a prompt.
        if sys.argv[2:3] == ["tool"]:
            from .tool_hook import main as tool_hook_main

            raise SystemExit(tool_hook_main())
        if sys.argv[2:3] != ["prompt"]:
            print("gingugu hook: expected 'prompt' or 'tool'", file=sys.stderr)
            raise SystemExit(2)
        from .prompt_hook import main as hook_main

        raise SystemExit(hook_main())
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
