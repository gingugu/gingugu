"""MCP server entry point.

Builds the MCP server (the SDK's ``MCPServer``) over stdio, wires up storage/namespace dependencies,
registers tool handlers, and runs. stdout is the MCP transport — all logging
goes to stderr (see config.setup_logging).
"""

from __future__ import annotations

import logging
import os

from mcp.server.mcpserver import MCPServer

from .cli_usage import USAGE
from .config import load_config
from .database import Database
from .embeddings import build_provider
from .handlers import ServerContext, register_all
from .namespaces import NamespaceManager
from .storage import MemoryStore

logger = logging.getLogger(__name__)


def build_server(transport: str = "stdio") -> MCPServer:
    """Construct and fully wire the MCP server (see ``build``)."""
    return build(transport)[0]


def build(transport: str = "stdio") -> tuple[MCPServer, ServerContext]:
    """Construct and fully wire the MCP server, and hand back its context.

    ``transport`` is "stdio" or "http"; `gingugu serve` passes "http" and keeps
    the context so the hook routes can reach the store's embedder and the DB path.
    """
    config = load_config()
    from .config import setup_logging
    from .handlers.fence import stdio_grant

    # Parsed before anything opens the database: a grant that cannot be
    # honoured stops the server here rather than serving the whole brain.
    grant = stdio_grant(config.grant) if transport == "stdio" else None

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
    if grant is not None:
        ctx.stdio_grant = grant
        if not grant.is_full:
            logger.info("stdio fenced by MEMORY_GRANT: %s", dict(grant.namespaces))

    from . import __version__
    from .board_events import BoardBus
    from .inbound import middleware

    # ``inbound.middleware`` gives the fence and the access log the request
    # and session each tool call arrived on. ``subscriptions/listen`` streams
    # are served from ``bus``: the board emits a resource-updated event into it
    # for every inbox a write changes.
    bus = BoardBus()
    ctx.bus = bus
    mcp = MCPServer("gingugu", version=__version__, subscriptions=bus, middleware=[middleware])
    register_all(mcp, ctx)
    return mcp, ctx


def main() -> None:
    """Console-script entry point.

    ``gingugu``         → run over stdio (default; local MCP client transport).
    ``gingugu serve``   → run over streamable HTTP for a remote/central brain.
    ``gingugu promote`` → promote local gold memories up to a central brain.
    ``gingugu init``    → bootstrap a repo's Claude Code hooks / rules file.
    ``gingugu harness`` → `init` plus the Claude Code harness (hooks, minions, skill, .ai/).
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
    if cmd == ["token"]:
        from .token_cli import main as token_main

        raise SystemExit(token_main(sys.argv[2:]))
    if cmd == ["credential"]:
        from .credential_cli import main as credential_main

        raise SystemExit(credential_main(sys.argv[2:]))
    if cmd == ["remote"]:
        from .remote import main as remote_main

        raise SystemExit(remote_main(sys.argv[2:]))
    if cmd == ["init"]:
        from .bootstrap import main as init_main

        raise SystemExit(init_main(sys.argv[2:]))
    if cmd == ["harness"]:
        from .bootstrap.harness import main as harness_main

        raise SystemExit(harness_main(sys.argv[2:]))
    if cmd == ["ui"]:
        from .webui import main as ui_main

        raise SystemExit(ui_main(sys.argv[2:]))
    if cmd == ["dream"]:
        from .dream_cli import main as dream_main

        raise SystemExit(dream_main(sys.argv[2:]))
    if cmd == ["board"]:
        if sys.argv[2:3] != ["watch"]:
            print("gingugu board: expected 'watch'", file=sys.stderr)
            raise SystemExit(2)
        from .board_watch import main as board_watch_main

        raise SystemExit(board_watch_main(sys.argv[3:]))
    if cmd == ["embed"]:
        from .embed_cli import main as embed_main

        raise SystemExit(embed_main(sys.argv[2:]))
    if cmd == ["hook"]:
        # Runs on the user's keystroke via a Claude Code hook. Reads the event
        # payload on stdin; never raises, never blocks a prompt.
        if sys.argv[2:3] == ["tool"]:
            from .tool_hook import main as tool_hook_main

            raise SystemExit(tool_hook_main())
        if sys.argv[2:3] == ["subagent"]:
            from .subagent_hook import main as subagent_hook_main

            raise SystemExit(subagent_hook_main())
        if sys.argv[2:3] == ["read"]:
            from .post_tool_hook import main as post_tool_hook_main

            raise SystemExit(post_tool_hook_main())
        if sys.argv[2:3] != ["prompt"]:
            print(
                "gingugu hook: expected 'prompt', 'tool', 'subagent' or 'read'",
                file=sys.stderr,
            )
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
    from . import remote

    try:
        target = remote.active()
    except ValueError as exc:
        print(f"gingugu: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    # Before serving: the client's hooks read this to fence themselves the way
    # this server is fenced (hook_grant.client_grant). Withdrawn only on a clean
    # shutdown, so a fenced server that fails leaves its fence behind.
    release = _publish_grant()
    if target is not None:
        from . import proxy
        from .config import load_config

        config = load_config()
        try:
            token = proxy.preflight(
                target, grant=config.grant, credentials_enabled=config.credentials_enabled
            )
        except proxy.ProxyRefused as exc:
            print(f"gingugu: remote brain {target.url}: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        try:
            code = proxy.serve_stdio(
                target.url,
                token,
                grant=config.grant or None,
                home=config.resolved_namespace,
                # The vault stays on this machine; the proxy serves it.
                vault_db=config.db_path if config.credentials_enabled else None,
            )
        except proxy.ProxyLost as exc:
            print(f"gingugu: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        release()
        raise SystemExit(code)
    try:
        server = build_server()
    except ValueError as exc:
        print(f"gingugu: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    server.run()
    release()


def _publish_grant():
    """Publish this server's grant; returns the clean-shutdown withdrawal.

    The import is guarded as well as the publish, so a broken install serves.
    """
    try:
        from . import client_grants

        path = client_grants.publish(os.environ.get("MEMORY_GRANT"))
    except ImportError as exc:
        logger.warning("hooks cannot be told this server's grant: %s", exc)
        return lambda: None
    client_grants.release_on_signal(path)
    return lambda: client_grants.release(path)


if __name__ == "__main__":
    main()
