"""MCP tool handlers, split by domain to honor the 300-line file limit.

Each handler module exposes a ``register(mcp, ctx)`` function that attaches its
tools to the shared FastMCP instance. ``ServerContext`` carries the wired-up
dependencies (DB connection, stores, config) into those closures.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..namespaces import NamespaceManager
from ..storage import MemoryStore


@dataclass
class ServerContext:
    config: Config
    store: MemoryStore
    namespaces: NamespaceManager
    conn: object  # sqlite3.Connection — kept loose to avoid import churn


def register_all(mcp, ctx: ServerContext) -> None:
    """Register every handler module's tools onto the FastMCP instance."""
    from . import admin, credentials, memory, recall, relations, search

    memory.register(mcp, ctx)
    recall.register(mcp, ctx)
    search.register(mcp, ctx)
    relations.register(mcp, ctx)
    admin.register(mcp, ctx)
    credentials.register(mcp, ctx)
