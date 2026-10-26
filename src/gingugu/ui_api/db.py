"""Read-only access for the Explorer API.

Never ``Database.connect()``: that migrates and creates files, and the Explorer
is an observer. ``mode=ro`` also means a bug here cannot write by accident.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..recall_sweep import sqlite_uri
from .env import ApiError

BUSY_TIMEOUT_MS = 5000
_NO_BRAIN = "no local brain at this path"


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """A fresh read-only connection; 503 when there is no DB file at the path."""
    if not Path(db_path).is_file():
        raise ApiError(503, _NO_BRAIN)
    try:
        conn = sqlite3.connect(sqlite_uri(db_path, "ro"), uri=True)
    except sqlite3.OperationalError as exc:
        raise ApiError(503, _NO_BRAIN) from exc
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def namespace_ids(conn: sqlite3.Connection, names: list[str]) -> list[str]:
    """Namespace names -> ids; an unknown name is a 400."""
    out = []
    for name in names:
        row = conn.execute("SELECT id FROM namespaces WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ApiError(400, f"unknown namespace: {name}")
        out.append(row["id"])
    return out


def parse_limit(params: dict[str, str], default: int, cap: int) -> int:
    """``limit`` as an int >= 1, clamped to ``cap``; anything else is a 400."""
    raw = params.get("limit")
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, "limit must be an integer") from None
    if value < 1:
        raise ApiError(400, "limit must be at least 1")
    return min(value, cap)
