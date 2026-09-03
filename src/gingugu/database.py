"""SQLite connection management.

Owns the single connection and the PRAGMAs every caller depends on: WAL mode,
foreign keys, and a busy timeout long enough to survive two sessions sharing a
brain. Opening a connection also brings the schema up to date - see
``gingugu.migrations`` for the migration registry and the runner.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .migrations import migrate

logger = logging.getLogger(__name__)


class Database:
    """Owns a single SQLite connection with WAL + foreign keys enabled."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        migrate(conn, db_path=self.db_path)
        self._conn = conn
        logger.info("Database ready at %s", self.db_path)
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self.connect()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
