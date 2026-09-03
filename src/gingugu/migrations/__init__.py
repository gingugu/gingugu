"""Schema migrations: the ordered registry and the runner that applies it.

Migrations are hand-rolled and keyed off ``PRAGMA user_version`` (Alembic is
overkill for a single-file DB). Each one is a ``(target_version, callable)``
pair in ``MIGRATIONS``, applied in order whenever the stored version is lower.

The migrations themselves live in two modules, split by what they actually do:

* ``schema`` - structural work. New tables, new columns, new indexes.
* ``claim_derivation`` - row work. Re-reading prose that never changed,
  because the claim extractor improved and stored rows carry the behaviour of
  whichever version wrote them.

A migration is append-only once released. ``migrate()`` selects pending work
with ``current < target``, so a DB already stamped at version N can never run
migration N again - a fix to an existing migration is permanently unreachable
on every store that already passed it. Reaching those stores needs a *new*
version number, which is why 006 exists at all.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from .claim_derivation import (
    _migration_005_claims,
    _migration_006_repair_claims_backfill,
    _migration_007_claim_precision,
    _migration_009_unverified_claims,
    _migration_010_claim_qualification,
)
from .schema import (
    _migration_001_initial_schema,
    _migration_002_credential_vault,
    _migration_003_tags_relations,
    _migration_004_embeddings,
    _migration_008_pinned,
)

logger = logging.getLogger(__name__)

# (target_version, migration_callable) — applied in order when current < target.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_initial_schema),
    (2, _migration_002_credential_vault),
    (3, _migration_003_tags_relations),
    (4, _migration_004_embeddings),
    (5, _migration_005_claims),
    (6, _migration_006_repair_claims_backfill),
    (7, _migration_007_claim_precision),
    (8, _migration_008_pinned),
    (9, _migration_009_unverified_claims),
    (10, _migration_010_claim_qualification),
]

# The version a fully-migrated DB lands on. Derived rather than written down so
# adding a migration cannot leave a stale literal behind — tests assert against
# this instead of hardcoding a number that every future bump would invalidate.
LATEST_SCHEMA_VERSION = MIGRATIONS[-1][0]


def _backup_before_migration(
    conn: sqlite3.Connection, db_path: Path, current_version: int, target_version: int
) -> Path | None:
    """Copy the DB to ``<name>.bak-before-vN`` before applying migrations.

    Skipped for in-memory DBs and for first-time DB creation
    (``current_version == 0``). If a backup file for this target version
    already exists (e.g. from a previous failed migration attempt) we leave
    it alone - overwriting could destroy the only known-good copy.

    Uses SQLite's own backup API rather than a file copy. We run in WAL mode,
    where committed transactions live in ``<db>-wal`` until a checkpoint folds
    them into the main file, so ``shutil.copy2`` captures the main file and
    silently leaves the newest writes behind - on a real brain that was 4.6MB of
    committed memories against a 20MB file. That matters more here than
    anywhere: this copy is the only safety net if the migration goes wrong, so a
    backup missing the most recent work is worse than useless. ``conn.backup``
    is WAL-aware and consistent under concurrent writers, which we have whenever
    two sessions share a brain.

    Returns the backup path on success, ``None`` if skipped.
    """
    if str(db_path) == ":memory:":
        return None
    if current_version == 0:  # fresh DB - nothing worth backing up yet
        return None
    if not db_path.exists():
        return None

    backup_path = db_path.with_name(f"{db_path.name}.bak-before-v{target_version}")
    if backup_path.exists():
        logger.info(
            "Pre-migration backup already exists at %s; leaving it intact",
            backup_path,
        )
        return backup_path

    with sqlite3.connect(backup_path) as dest:
        conn.backup(dest)
    logger.info(
        "Pre-migration backup created: %s (v%d -> v%d)",
        backup_path,
        current_version,
        target_version,
    )
    return backup_path


def migrate(conn: sqlite3.Connection, db_path: Path | None = None) -> int:
    """Apply pending migrations. Returns the resulting schema version.

    When ``db_path`` is provided and migrations are pending, a one-shot
    backup of the live DB file is taken before the first migration runs
    (``<db>.bak-before-vN`` where N is the first pending target). The
    backup is best-effort: if it fails the migration still proceeds.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [(t, fn) for t, fn in MIGRATIONS if current < t]
    if pending and db_path is not None:
        try:
            _backup_before_migration(conn, db_path, current, pending[0][0])
        except (OSError, sqlite3.Error) as e:  # disk full, permissions, locked source
            logger.warning("Pre-migration backup failed (continuing): %s", e)
    for target, fn in pending:
        logger.info("Applying migration -> v%d", target)
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
        conn.commit()
        current = target
    return current
