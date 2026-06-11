"""Namespace CRUD and auto-detection.

Resolution order (first hit wins): explicit arg → config namespace
(MEMORY_NAMESPACE) → basename of MEMORY_NAMESPACE_PATH → ``default``.
See docs/architecture.md → Namespace Auto-Detection.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from .config import Config
from .models import Namespace, utcnow_iso

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "default"


class NamespaceManager:
    def __init__(self, conn: sqlite3.Connection, config: Config) -> None:
        self._conn = conn
        self._config = config

    def _row_to_model(self, row: sqlite3.Row) -> Namespace:
        return Namespace(**dict(row))

    def resolve_name(self, explicit: str | None = None) -> str:
        """Resolve the effective namespace name for a request."""
        if explicit:
            return explicit
        resolved = self._config.resolved_namespace
        if resolved:
            return resolved
        logger.warning("No namespace configured; falling back to %r", DEFAULT_NAMESPACE)
        return DEFAULT_NAMESPACE

    def get_or_create(
        self, name: str, path: str | None = None, description: str | None = None
    ) -> Namespace:
        """Fetch a namespace by name, creating it if absent."""
        row = self._conn.execute("SELECT * FROM namespaces WHERE name = ?", (name,)).fetchone()
        if row is not None:
            return self._row_to_model(row)

        now = utcnow_iso()
        ns = Namespace(
            id=str(uuid.uuid4()),
            name=name,
            path=path,
            description=description,
            created_at=now,
            updated_at=now,
        )
        self._conn.execute(
            "INSERT INTO namespaces(id, name, path, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ns.id, ns.name, ns.path, ns.description, ns.created_at, ns.updated_at),
        )
        self._conn.commit()
        logger.info("Created namespace %r (%s)", name, ns.id)
        return ns

    def list(self) -> list[Namespace]:
        rows = self._conn.execute("SELECT * FROM namespaces ORDER BY name").fetchall()
        return [self._row_to_model(r) for r in rows]

    def get(self, name: str) -> Namespace | None:
        """Fetch a namespace by name, or None if it does not exist."""
        row = self._conn.execute("SELECT * FROM namespaces WHERE name = ?", (name,)).fetchone()
        return self._row_to_model(row) if row is not None else None

    def count_memories(self, namespace_id: str) -> int:
        """Number of memories in a namespace (used by delete guards)."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM memories WHERE namespace_id = ?", (namespace_id,)
        ).fetchone()[0]

    def update(
        self, name: str, *, path: str | None = None, description: str | None = None
    ) -> Namespace | None:
        """Update a namespace's path/description. Returns None if it doesn't exist.

        Only non-None arguments are applied (existing values are preserved).
        """
        existing = self.get(name)
        if existing is None:
            return None
        now = utcnow_iso()
        self._conn.execute(
            "UPDATE namespaces SET path = ?, description = ?, updated_at = ? WHERE id = ?",
            (
                path if path is not None else existing.path,
                description if description is not None else existing.description,
                now,
                existing.id,
            ),
        )
        self._conn.commit()
        logger.info("Updated namespace %r", name)
        return self.get(name)

    def delete(self, name: str, *, cascade: bool = False) -> int:
        """Delete a namespace. Returns the number of memories removed.

        Guards: the ``default`` namespace cannot be deleted, and a non-empty
        namespace is refused unless ``cascade=True`` (deletion cascades to its
        memories, tags links, relations, and access log via FK ``ON DELETE
        CASCADE``). Raises ``ValueError`` on a guard violation or unknown name.
        """
        if name == DEFAULT_NAMESPACE:
            raise ValueError(f"cannot delete the {DEFAULT_NAMESPACE!r} namespace")
        existing = self.get(name)
        if existing is None:
            raise ValueError(f"namespace {name!r} not found")
        memory_count = self.count_memories(existing.id)
        if memory_count > 0 and not cascade:
            raise ValueError(
                f"namespace {name!r} has {memory_count} memories; "
                "pass cascade=True to delete them too"
            )
        self._conn.execute("DELETE FROM namespaces WHERE id = ?", (existing.id,))
        self._conn.commit()
        logger.info("Deleted namespace %r (cascaded %d memories)", name, memory_count)
        return memory_count
