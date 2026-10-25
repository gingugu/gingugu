"""The Memory Explorer's read API.

One entry point, ``dispatch``, serves both modes: ``gingugu ui`` calls it
against the local DB, and ``gingugu serve`` calls it for ``/ui/api/*`` on a
remote brain. Everything here is an observer - read-only connection, no access
credit, no query log - so opening the Explorer never changes the brain it shows.
It never raises: every failure is a status and a JSON body.
"""

from __future__ import annotations

import logging
import sqlite3

from .db import open_readonly
from .detail import memory_detail
from .env import ApiError, UiEnv
from .events import events
from .graph import graph, info
from .lens import lens_search, stats

logger = logging.getLogger(__name__)

__all__ = ["UiEnv", "dispatch"]

_SIMPLE = {"/info": info, "/graph": graph, "/stats": stats, "/events": events}
_MEMORY_PREFIX = "/memory/"


def _known(path: str) -> bool:
    if path in _SIMPLE or path == "/search":
        return True
    return path.startswith(_MEMORY_PREFIX) and len(path) > len(_MEMORY_PREFIX)


def _route(conn: sqlite3.Connection, path: str, params: dict[str, str], env: UiEnv) -> dict:
    if path in _SIMPLE:
        return _SIMPLE[path](conn, params)
    if path == "/search":
        return lens_search(conn, params, env)
    return memory_detail(conn, path[len(_MEMORY_PREFIX) :])


def dispatch(path: str, params: dict[str, str], env: UiEnv) -> tuple[int, dict]:
    """Answer one API request as ``(status, json_body)``. Never raises."""
    try:
        if not _known(path):
            raise ApiError(404, f"unknown route: {path}")
        conn = open_readonly(env.db_path)
        try:
            return 200, _route(conn, path, params, env)
        finally:
            conn.close()
    except ApiError as exc:
        return exc.status, {"ok": False, "error": exc.message}
    except Exception:  # noqa: BLE001 - the API never crashes its host
        logger.exception("ui api %s failed", path)
        return 500, {"ok": False, "error": "internal error"}
