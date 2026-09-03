"""Upgrade-migration tests — the path every existing user hits.

The suite elsewhere builds fresh (v0 -> v3) DBs; here we stand up an older
v2 database *with data* and prove migrate() carries it forward to v3 without
loss and creates the new tags/relations tables."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from gingugu.migrations import LATEST_SCHEMA_VERSION, migrate
from gingugu.migrations.schema import (
    _migration_001_initial_schema,
    _migration_002_credential_vault,
)
from gingugu.models import utcnow_iso


def _apply_through(conn: sqlite3.Connection, version: int) -> None:
    """Bring a bare connection up to `version` without running later migrations."""
    from gingugu.migrations import MIGRATIONS

    for target, fn in MIGRATIONS:
        if target > version:
            break
        fn(conn)
        conn.execute(f"PRAGMA user_version = {target}")
    conn.commit()


def _open_v2(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _migration_001_initial_schema(conn)
    _migration_002_credential_vault(conn)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    return conn


def _seed_v2(conn: sqlite3.Connection) -> tuple[str, str]:
    now = utcnow_iso()
    ns_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO namespaces(id, name, path, description, created_at, updated_at) "
        "VALUES (?, 'legacy', NULL, NULL, ?, ?)",
        (ns_id, now, now),
    )
    mem_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO memories(id, namespace_id, type, title, content, confidence, source, "
        "created_at, updated_at, last_accessed, last_confirmed, access_count, metadata) "
        "VALUES (?, ?, 'fact', 'old memory', 'pre-existing content', 'verified', NULL, "
        "?, ?, ?, NULL, 0, NULL)",
        (mem_id, ns_id, now, now, now),
    )
    conn.commit()
    return ns_id, mem_id


def test_v2_to_v3_upgrade_preserves_data(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = _open_v2(path)
    ns_id, mem_id = _seed_v2(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2

    # Upgrade.
    final = migrate(conn)
    assert final == LATEST_SCHEMA_VERSION
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION

    # New tables exist.
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"tags", "memory_tags", "relations"}.issubset(tables)

    # Pre-existing data survived untouched.
    mem = conn.execute("SELECT title, content FROM memories WHERE id = ?", (mem_id,)).fetchone()
    assert mem["title"] == "old memory"
    assert mem["content"] == "pre-existing content"
    assert conn.execute("SELECT name FROM namespaces WHERE id = ?", (ns_id,)).fetchone()[0] == (
        "legacy"
    )
    conn.close()


def test_migrate_is_idempotent_when_current(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = _open_v2(path)
    _seed_v2(conn)
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    # Running again is a no-op (no error, stays at the current version).
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    conn.close()


def test_v5_backfills_claims_for_existing_memories() -> None:
    """Migration 005 must POPULATE, not just create the table.

    Claims are otherwise written only on store/update, so every existing user
    would upgrade into an empty table and the feature would do nothing until
    they happened to edit a memory. Migration 004 solves the same shape with a
    startup backfill; claim extraction is pure regex (~210ms for 735 memories)
    so it belongs in the migration, where user_version guarantees exactly one run.
    """
    conn = sqlite3.connect(":memory:")  # deliberately NO row_factory
    _apply_through(conn, 4)
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("ns1", "gingugu", "2026-01-01", "2026-01-01"),
    )
    for mid, title, content in (
        ("m1", "PR #10 open", "PR #10, open, NOT merged yet"),
        ("m2", "released", "PR #10 merged to main"),
        ("m3", "no refs", "just some prose about tuning"),
    ):
        conn.execute(
            "INSERT INTO memories(id, namespace_id, type, title, content, confidence, "
            "created_at, updated_at, last_accessed, access_count) VALUES (?,?,?,?,?,?,?,?,?,0)",
            (
                mid,
                "ns1",
                "workflow",
                title,
                content,
                "verified",
                "2026-01-01",
                "2026-01-01",
                "2026-01-01",
            ),
        )

    assert migrate(conn) == LATEST_SCHEMA_VERSION

    rows = conn.execute(
        "SELECT memory_id, ref, state FROM memory_claims ORDER BY memory_id"
    ).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("m1", "gingugu#10", "open"),
        ("m2", "gingugu#10", "resolved"),
    ]  # m3 has no refs, so no rows - and that is not the same as "unprocessed"


def test_v5_backfill_runs_exactly_once() -> None:
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 4)
    conn.execute(
        "INSERT INTO namespaces(id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("ns1", "gingugu", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO memories(id, namespace_id, type, title, content, confidence, "
        "created_at, updated_at, last_accessed, access_count) VALUES (?,?,?,?,?,?,?,?,?,0)",
        (
            "m1",
            "ns1",
            "workflow",
            "t",
            "PR #10 open",
            "verified",
            "2026-01-01",
            "2026-01-01",
            "2026-01-01",
        ),
    )
    migrate(conn)
    before = conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0]
    migrate(conn)  # user_version is already current - must not duplicate
    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == before == 1


def test_v5_on_a_fresh_database_is_a_no_op() -> None:
    conn = sqlite3.connect(":memory:")
    assert migrate(conn) == LATEST_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == 0


# --- migration 006: repairing DBs stranded at v5 with an empty claims table ---


def _strand_at_v5(conn: sqlite3.Connection) -> None:
    """Reproduce a DB that reached v5 *before* 005 learned to backfill.

    That is what pre-fix branch code did to a live DB: created the table and
    stamped the version, leaving nothing to trigger the backfill ever again.
    """
    from gingugu.migrations.claim_derivation import _SCHEMA_V5

    _apply_through(conn, 4)
    conn.executescript(_SCHEMA_V5)
    conn.execute("PRAGMA user_version = 5")
    conn.commit()


def _seed_claim_memories(conn: sqlite3.Connection, rows: tuple[tuple[str, str, str], ...]) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO namespaces(id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("ns1", "gingugu", "2026-01-01", "2026-01-01"),
    )
    for mid, title, content in rows:
        conn.execute(
            "INSERT INTO memories(id, namespace_id, type, title, content, confidence, "
            "created_at, updated_at, last_accessed, access_count) VALUES (?,?,?,?,?,?,?,?,?,0)",
            (
                mid,
                "ns1",
                "workflow",
                title,
                content,
                "verified",
                "2026-01-01",
                "2026-01-01",
                "2026-01-01",
            ),
        )
    conn.commit()


def test_v6_repairs_a_db_stranded_at_v5_with_an_empty_claims_table() -> None:
    """The whole reason 006 exists.

    migrate() picks pending work with ``current < target``, so a DB already
    stamped 5 can never re-run 005 no matter how many times it reconnects.
    Only a new version number reaches it.
    """
    conn = sqlite3.connect(":memory:")  # deliberately NO row_factory
    _strand_at_v5(conn)
    _seed_claim_memories(
        conn,
        (
            ("m1", "PR #10 open", "PR #10, open, NOT merged yet"),
            ("m2", "released", "PR #10 merged to main"),
        ),
    )
    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == 0

    assert migrate(conn) == LATEST_SCHEMA_VERSION

    rows = conn.execute("SELECT memory_id, ref, state FROM memory_claims ORDER BY memory_id")
    assert [tuple(r) for r in rows] == [
        ("m1", "gingugu#10", "open"),
        ("m2", "gingugu#10", "resolved"),
    ]


def test_v6_repairs_a_stranded_db_that_is_no_longer_empty() -> None:
    """Why 006 is unconditional instead of guarded on an empty table.

    A stranded DB that has since stored one memory containing a ref has a
    non-empty claims table while the rest of the corpus is still unprocessed.
    An emptiness guard would skip it permanently.
    """
    conn = sqlite3.connect(":memory:")
    _strand_at_v5(conn)
    _seed_claim_memories(
        conn,
        (
            ("old", "pre-existing, never processed", "PR #10 open"),
            ("new", "stored after stranding", "PR #22 open"),
        ),
    )
    conn.execute(
        "INSERT INTO memory_claims (id, memory_id, kind, ref, state, created_at) "
        "VALUES ('c1', 'new', 'pr', 'gingugu#22', 'open', '2026-01-02')"
    )
    conn.commit()

    assert migrate(conn) == LATEST_SCHEMA_VERSION

    refs = {r[0] for r in conn.execute("SELECT ref FROM memory_claims")}
    assert refs == {"gingugu#10", "gingugu#22"}


def test_v6_preserves_existing_resolution_state() -> None:
    """Idempotence must not cost a reconciliation.

    ``INSERT OR IGNORE`` against UNIQUE (memory_id, kind, ref) leaves an
    already-resolved claim alone. Clobbering resolved_* here would silently
    reopen every claim a user had reconciled.
    """
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 5)
    _seed_claim_memories(conn, (("m1", "PR #10 open", "PR #10 open"),))
    conn.execute(
        "INSERT INTO memory_claims (id, memory_id, kind, ref, state, resolved_state, "
        "resolved_by, resolved_at, created_at) VALUES "
        "('c1', 'm1', 'pr', 'gingugu#10', 'open', 'resolved', NULL, '2026-02-01', '2026-01-01')"
    )
    conn.commit()

    assert migrate(conn) == LATEST_SCHEMA_VERSION

    rows = conn.execute("SELECT resolved_state, resolved_at FROM memory_claims").fetchall()
    assert rows == [("resolved", "2026-02-01")]


def test_v6_does_not_resurrect_a_ref_edited_out_of_a_memory() -> None:
    """Claims are re-derived from CURRENT text, so a removed ref stays removed."""
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 5)
    _seed_claim_memories(conn, (("m1", "no refs anymore", "the PR reference was edited out"),))
    conn.commit()

    assert migrate(conn) == LATEST_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == 0


# --- migration 007: claim-extraction precision ------------------------------


def _rename_namespace(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("UPDATE namespaces SET name = ? WHERE id = 'ns1'", (name,))
    conn.commit()


def test_v7_adds_default_repo_and_seeds_the_non_repo_namespaces() -> None:
    """``crow`` and ``default`` are gingugu's own non-repo namespaces."""
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 6)
    for ns_id, name in (("nsA", "crow"), ("nsB", "gingugu")):
        conn.execute(
            "INSERT OR IGNORE INTO namespaces(id, name, created_at, updated_at) VALUES (?,?,?,?)",
            (ns_id, name, "2026-01-01", "2026-01-01"),
        )
    conn.commit()

    assert migrate(conn) == LATEST_SCHEMA_VERSION

    seeded = dict(conn.execute("SELECT name, default_repo FROM namespaces").fetchall())
    assert seeded["crow"] == ""
    assert seeded["gingugu"] is None  # unset: still falls back to its own name


def test_v7_prunes_a_phantom_claim_that_came_from_a_wikilink() -> None:
    """The defect that shipped in 0.10.0.

    The phantom row is seeded directly because 005's backfill now runs the
    *fixed* extractor and would never create one. This is what a real 0.10.x
    database looks like on disk.
    """
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 6)
    _seed_claim_memories(
        conn, (("m1", "RESOLVED: the crashloop", "See [[PR #155 OPEN, merge HELD]]."),)
    )
    conn.execute(
        "INSERT INTO memory_claims (id, memory_id, kind, ref, state, created_at) "
        "VALUES ('c1','m1','pr','gingugu#155','open','2026-01-01')"
    )
    conn.commit()

    assert migrate(conn) == LATEST_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == 0


def test_v7_drops_bare_refs_in_a_namespace_that_is_not_a_repo() -> None:
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 5)
    _seed_claim_memories(conn, (("m1", "Reflection", "PR #167 is still open"),))
    _rename_namespace(conn, "crow")

    assert migrate(conn) == LATEST_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == 0


def test_v7_keeps_bare_refs_in_a_real_repo_namespace() -> None:
    """The namespace default is load-bearing — 007 must not weaken it."""
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 5)
    _seed_claim_memories(conn, (("m1", "PR #20 open", "PR #20 is still open"),))
    conn.commit()

    assert migrate(conn) == LATEST_SCHEMA_VERSION
    assert [tuple(r) for r in conn.execute("SELECT ref, state FROM memory_claims")] == [
        ("gingugu#20", "open")
    ]


def test_v7_preserves_resolution_on_a_claim_it_keeps() -> None:
    """The reason 007 does not go through ``claim_sync.sync_claims``.

    That path drops resolution deliberately, because it runs when the *prose*
    changed. Here the prose is untouched and only the extractor improved, so
    discarding resolution would destroy manual reconciliation work that cannot
    be recovered.
    """
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 5)
    _seed_claim_memories(conn, (("m1", "PR #20 open", "PR #20 is still open"),))
    conn.execute(
        "INSERT INTO memory_claims (id, memory_id, kind, ref, state, resolved_state, "
        "resolved_at, created_at) VALUES "
        "('c1','m1','pr','gingugu#20','open','resolved','2026-02-01','2026-01-01')"
    )
    conn.commit()

    assert migrate(conn) == LATEST_SCHEMA_VERSION

    rows = conn.execute("SELECT resolved_state, resolved_at FROM memory_claims").fetchall()
    assert [tuple(r) for r in rows] == [("resolved", "2026-02-01")]


def test_v7_rederive_is_idempotent() -> None:
    """Re-running prunes nothing the second time."""
    from gingugu import claim_rederive

    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 6)
    _seed_claim_memories(
        conn,
        (
            ("m1", "PR #20 open", "PR #20 is still open"),
            ("m2", "a link", "see [[PR #99 open: something]]"),
        ),
    )
    migrate(conn)

    pruned, written = claim_rederive.rederive_claims(conn)
    assert (pruned, written) == (0, 1)


# --- migration 009: the unverified claim state -------------------------------


def test_v9_records_a_state_less_ref_that_was_previously_dropped() -> None:
    """The gap 009 closes, seeded exactly as a real 0.16.x database holds it.

    ``_apply_through(8)`` runs 005's backfill with the OLD extractor, so the
    ref is absent from the table before 009 runs — which is what made it
    invisible on disk, not merely uncounted.
    """
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 8)
    _seed_claim_memories(conn, (("m1", "deliverables", "Branch done, PR #40: shipped it"),))

    assert conn.execute("SELECT COUNT(*) FROM memory_claims").fetchone()[0] == 0

    assert migrate(conn) == LATEST_SCHEMA_VERSION

    rows = conn.execute("SELECT ref, state FROM memory_claims").fetchall()
    assert [tuple(r) for r in rows] == [("gingugu#40", "unverified")]


def test_v9_leaves_the_open_backlog_untouched() -> None:
    """The regression that matters most: 009 must add rows, never reclassify.

    An open claim silently becoming unverified would empty the reconciliation
    backlog and look like progress.
    """
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 8)
    _seed_claim_memories(
        conn,
        (
            ("m1", "session log", "PR #20 is still open"),
            ("m2", "notes", "see PR #40 for context"),
        ),
    )

    assert migrate(conn) == LATEST_SCHEMA_VERSION

    states = dict(conn.execute("SELECT ref, state FROM memory_claims").fetchall())
    assert states == {"gingugu#20": "open", "gingugu#40": "unverified"}


def test_v9_preserves_resolution_on_an_existing_claim() -> None:
    """009 goes through ``claim_rederive`` for 007's reason: prose is untouched."""
    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 8)
    _seed_claim_memories(conn, (("m1", "PR #20 open", "PR #20 is still open. Also see PR #40."),))
    # Seeded directly: memories inserted by SQL never ran the extractor, which
    # is exactly the on-disk shape of a 0.16.x database carrying manual
    # reconciliation work.
    conn.execute(
        "INSERT INTO memory_claims (id, memory_id, kind, ref, state, resolved_state, "
        "resolved_at, created_at) VALUES "
        "('c1','m1','pr','gingugu#20','open','resolved','2026-02-01','2026-01-01')"
    )
    conn.commit()

    assert migrate(conn) == LATEST_SCHEMA_VERSION

    rows = conn.execute(
        "SELECT ref, resolved_state, resolved_at FROM memory_claims ORDER BY ref"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("gingugu#20", "resolved", "2026-02-01"),
        ("gingugu#40", None, None),
    ]


def test_v9_prunes_nothing_and_is_idempotent() -> None:
    """009 only ever adds: every ref it records is one the old extractor dropped."""
    from gingugu import claim_rederive

    conn = sqlite3.connect(":memory:")
    _apply_through(conn, 8)
    _seed_claim_memories(
        conn,
        (
            ("m1", "session log", "PR #20 is still open"),
            ("m2", "notes", "see PR #40 for context"),
        ),
    )
    migrate(conn)

    pruned, written = claim_rederive.rederive_claims(conn)
    assert (pruned, written) == (0, 2)
