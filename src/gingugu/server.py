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

    ctx = ServerContext(
        config=config,
        store=MemoryStore(conn),
        namespaces=NamespaceManager(conn, config),
        conn=conn,
    )

    mcp = FastMCP("gingugu")
    register_all(mcp, ctx)
    return mcp


def main() -> None:
    """Console-script entry point: run the server over stdio."""
    build_server().run()


if __name__ == "__main__":
    main()
