"""MCP tool handlers, split by domain to honor the 300-line file limit.

Each handler module exposes a ``register(mcp, ctx)`` function that attaches its
tools to the shared FastMCP instance. ``ServerContext`` carries the wired-up
dependencies (DB connection, stores, config) into those closures.
"""

from __future__ import annotations

import functools
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


class _HeartbeatMCP:
    """Proxy that stamps the activity heartbeat around every registered tool.

    Wrapping the ``tool`` decorator once here, rather than editing thirteen
    handler modules, is what makes the heartbeat impossible to forget: a tool
    added next year is instrumented by the act of registering it. Nothing in a
    handler knows this exists, and nothing has to.

    Only ``tool`` is intercepted; every other attribute passes straight through
    to the real FastMCP instance.
    """

    def __init__(self, mcp, conn) -> None:
        self._mcp = mcp
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._mcp, name)

    def tool(self, *d_args, **d_kwargs):
        inner = self._mcp.tool(*d_args, **d_kwargs)
        conn = self._conn

        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                from ..activity import stamp

                try:
                    return fn(*args, **kwargs)
                finally:
                    # In a finally, so a tool that raised still counts as the
                    # user reaching for the brain. Stamping only on success
                    # would let a session of failing calls read as idle and
                    # invite a background pass into the middle of it.
                    stamp(conn, fn.__name__)

            return inner(wrapper)

        return decorator


def register_all(mcp, ctx: ServerContext) -> None:
    """Register every handler module's tools onto the FastMCP instance.

    Handlers receive a ``_HeartbeatMCP`` proxy rather than the raw server, so
    every tool they attach records that the brain was used. See ``activity``.
    """
    mcp = _HeartbeatMCP(mcp, ctx.conn)
    from . import (
        admin,
        consolidate,
        credentials,
        dream,
        excerpt,
        forget,
        memory,
        recall,
        relations,
        search,
    )

    memory.register(mcp, ctx)
    forget.register(mcp, ctx)
    recall.register(mcp, ctx)
    search.register(mcp, ctx)
    excerpt.register(mcp, ctx)
    relations.register(mcp, ctx)
    consolidate.register(mcp, ctx)
    dream.register(mcp, ctx)
    admin.register(mcp, ctx)
    # Credential vault is opt-out: a shared/central instance runs with
    # MEMORY_CREDENTIALS_ENABLED=false so it never exposes secret tools.
    if ctx.config.credentials_enabled:
        credentials.register(mcp, ctx)
