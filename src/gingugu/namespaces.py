"""Namespace CRUD and auto-detection.

Resolution order (first hit wins): explicit arg → config namespace
(MEMORY_NAMESPACE) → basename of MEMORY_NAMESPACE_PATH → ``default``.
See docs/architecture.md → Namespace Auto-Detection.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from . import grants
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
        """Resolve the effective namespace name for a request.

        Under a scoped token whose grant does not cover the server's default,
        an omitted namespace is an error that does not name the default -
        echoing it back would disclose a namespace the token cannot see.
        """
        if explicit:
            return explicit
        resolved = grants.home() or self._config.resolved_namespace
        if not resolved:
            logger.warning("No namespace configured; falling back to %r", DEFAULT_NAMESPACE)
            resolved = DEFAULT_NAMESPACE
        if not grants.can_read_name(resolved):
            raise grants.AccessDenied("namespace is required for this token")
        return resolved

    def get_or_create(
        self,
        name: str,
        path: str | None = None,
        description: str | None = None,
        default_repo: str | None = None,
    ) -> Namespace:
        """Fetch a namespace by name, creating it if absent.

        Under a scoped grant, a namespace the grant cannot read is "not found"
        whether or not it exists, and minting one takes write on that name.
        """
        if not grants.can_read_name(name):
            raise grants.AccessDenied(f"namespace {name!r} not found")
        row = self._conn.execute("SELECT * FROM namespaces WHERE name = ?", (name,)).fetchone()
        if row is not None:
            return self._row_to_model(row)
        if not grants.can_write_name(name):
            raise grants.AccessDenied(f"namespace {name!r} not found")

        now = utcnow_iso()
        ns = Namespace(
            id=str(uuid.uuid4()),
            name=name,
            path=path,
            description=description,
            default_repo=default_repo,
            created_at=now,
            updated_at=now,
        )
        self._conn.execute(
            "INSERT INTO namespaces"
            "(id, name, path, description, default_repo, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ns.id,
                ns.name,
                ns.path,
                ns.description,
                ns.default_repo,
                ns.created_at,
                ns.updated_at,
            ),
        )
        self._conn.commit()
        logger.info("Created namespace %r (%s)", name, ns.id)
        return ns

    def list(self) -> list[Namespace]:
        """Every namespace this call can see - all of them unless fenced."""
        rows = self._conn.execute("SELECT * FROM namespaces ORDER BY name").fetchall()
        return [self._row_to_model(r) for r in rows if grants.can_read_name(r["name"])]

    def get(self, name: str) -> Namespace | None:
        """Fetch a namespace by name, or None if it does not exist (or the
        call's grant cannot read it - the two are deliberately the same)."""
        if not grants.can_read_name(name):
            return None
        row = self._conn.execute("SELECT * FROM namespaces WHERE name = ?", (name,)).fetchone()
        return self._row_to_model(row) if row is not None else None

    def count_memories(self, namespace_id: str) -> int:
        """Number of memories in a namespace (used by delete guards)."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM memories WHERE namespace_id = ?", (namespace_id,)
        ).fetchone()[0]

    def update(
        self,
        name: str,
        *,
        path: str | None = None,
        description: str | None = None,
        default_repo: str | None = None,
    ) -> Namespace | None:
        """Update a namespace's path/description/default_repo.

        Returns None if the namespace doesn't exist. Only non-None arguments
        are applied (existing values are preserved).

        ``default_repo=""`` is meaningful, not a no-op: it declares that this
        namespace is not a repo, so bare "PR #12" refs are dropped instead of
        keyed to a repo of the same name. See ``claim_sync``.
        """
        existing = self.get(name)
        if existing is None:
            return None
        if not grants.can_write_name(name):
            raise grants.AccessDenied(f"namespace {name!r} is read-only for this token")
        now = utcnow_iso()
        new_default = default_repo if default_repo is not None else existing.default_repo
        self._conn.execute(
            "UPDATE namespaces SET path = ?, description = ?, default_repo = ?, "
            "updated_at = ? WHERE id = ?",
            (
                path if path is not None else existing.path,
                description if description is not None else existing.description,
                new_default,
                now,
                existing.id,
            ),
        )
        if new_default != existing.default_repo:
            self._rederive_claims(existing.id, name)
        self._conn.commit()
        logger.info("Updated namespace %r", name)
        return self.get(name)

    def _rederive_claims(self, namespace_id: str, name: str) -> None:
        """Re-key this namespace's claims after ``default_repo`` changed.

        Without this the declaration is inert: claims are stored rows and the
        default repo is only read at extraction time, so every already-derived
        ref keeps whatever key it was given. A user declaring a namespace
        non-repo would watch nothing happen, with no supported way to apply it
        short of editing prose — the exact dodge the claims design exists to
        make unnecessary.

        Best-effort, like every other claim path: a namespace update must not
        fail over a hint.
        """
        from . import claim_rederive

        try:
            pruned, written = claim_rederive.rederive_claims(self._conn, namespace_id=namespace_id)
            logger.info(
                "default_repo changed for %r: re-derived claims (%d pruned, %d written)",
                name,
                pruned,
                written,
            )
        except Exception:  # noqa: BLE001 - never fail an update over a hint
            logger.warning("claim re-derive failed for namespace %r", name, exc_info=True)

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
        if not grants.can_write_name(name):
            raise grants.AccessDenied(f"namespace {name!r} is read-only for this token")
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
