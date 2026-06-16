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


def main() -> None:
    """Console-script entry point: run the server over stdio."""
    build_server().run()


if __name__ == "__main__":
    main()
