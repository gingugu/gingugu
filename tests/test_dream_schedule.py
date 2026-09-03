"""Tests for when the dream pass is allowed to run, and what stops it.

The design claim under test is that an OS scheduler plus a self-gating command
is enough, and no daemon is needed. That claim rests on three behaviours: the
command declines to run while someone is working, it declines to run alongside
another pass, and a run already under way stops when the user comes back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gingugu import activity, dream_lock
from gingugu.database import Database
from gingugu.dream_schedule import RAN, SKIPPED_ACTIVE, SKIPPED_LOCKED, guarded_run
from gingugu.models import MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore


def _idle_by(db: Database, delta: timedelta) -> None:
    db.conn.execute(
        "UPDATE activity SET last_active_at = ? WHERE id = 1",
        ((datetime.now(UTC) - delta).isoformat(),),
    )
    db.conn.commit()


def _graph(store: MemoryStore, relations: RelationManager, ns_id: str) -> None:
    """A hub and spokes - enough structure for every pass to find something."""
    hub = store.create(namespace_id=ns_id, type=MemoryType.FACT, title="hub", content="centre").id
    for i in range(6):
        leaf = store.create(
            namespace_id=ns_id, type=MemoryType.FACT, title=f"leaf-{i}", content="spoke"
        ).id
        relations.relate(source_id=leaf, target_id=hub, relation_type=RelationType.CAUSED_BY)


def test_hand_run_ignores_the_idle_gate(db: Database) -> None:
    """``idle_seconds=None`` means "I asked for this deliberately"."""
    _idle_by(db, timedelta(seconds=0))

    assert guarded_run(db.conn)["outcome"] == RAN


def test_scheduled_run_skips_while_the_brain_is_active(db: Database) -> None:
    _idle_by(db, timedelta(minutes=2))

    report = guarded_run(db.conn, idle_seconds=20 * 60)

    assert report["outcome"] == SKIPPED_ACTIVE
    assert report["idle_seconds"] > 60
    assert "passes" not in report


def test_scheduled_run_proceeds_once_quiet(db: Database) -> None:
    _idle_by(db, timedelta(minutes=45))

    report = guarded_run(db.conn, idle_seconds=20 * 60)

    assert report["outcome"] == RAN
    assert report["cancelled"] is False


def test_run_skips_when_another_pass_holds_the_lock(db: Database) -> None:
    _idle_by(db, timedelta(minutes=45))
    dream_lock.acquire(db.conn)

    assert guarded_run(db.conn, idle_seconds=20 * 60)["outcome"] == SKIPPED_LOCKED


def test_lock_is_released_after_a_run(db: Database) -> None:
    assert guarded_run(db.conn)["outcome"] == RAN
    assert guarded_run(db.conn)["outcome"] == RAN


def test_activity_mid_run_cancels_the_remaining_passes(
    db: Database,
    store: MemoryStore,
    namespaces: NamespaceManager,
    relations: RelationManager,
    monkeypatch,
) -> None:
    """Coming back to the keyboard stops a run already under way.

    The user "returns" between the first and second pass: the gate lets the run
    start, then the next check finds the brain active and the run stops there.
    """
    ns = namespaces.get_or_create("sched-ns")
    _graph(store, relations, ns.id)
    _idle_by(db, timedelta(minutes=45))

    real_is_idle = activity.is_idle_for
    checks = {"n": 0}

    def is_idle_until_second_check(conn, threshold):
        checks["n"] += 1
        if checks["n"] > 2:  # gate, first pass, then the user is back
            return False
        return real_is_idle(conn, threshold)

    monkeypatch.setattr(activity, "is_idle_for", is_idle_until_second_check)
    report = guarded_run(db.conn, idle_seconds=20 * 60)

    assert report["outcome"] == RAN
    assert report["cancelled"] is True
    # Whatever finished before the stop is kept, not rolled back.
    assert 0 < len(report["passes"]) < 3


def test_a_cancelled_run_leaves_a_smaller_queue_not_a_broken_one(
    db: Database,
    store: MemoryStore,
    namespaces: NamespaceManager,
    relations: RelationManager,
    monkeypatch,
) -> None:
    """The next run must be able to finish what a cancelled one started."""
    ns = namespaces.get_or_create("resume-ns")
    _graph(store, relations, ns.id)
    _idle_by(db, timedelta(minutes=45))

    real_is_idle = activity.is_idle_for
    checks = {"n": 0}

    def cancel_after_first_pass(conn, threshold):
        checks["n"] += 1
        return real_is_idle(conn, threshold) if checks["n"] <= 2 else False

    monkeypatch.setattr(activity, "is_idle_for", cancel_after_first_pass)
    cancelled = guarded_run(db.conn, idle_seconds=20 * 60)
    assert cancelled["cancelled"] is True
    partial = len(cancelled["passes"])

    monkeypatch.undo()
    full = guarded_run(db.conn)

    assert full["outcome"] == RAN
    assert len(full["passes"]) == 3
    assert partial < 3


def test_embedder_is_not_built_when_the_gate_refuses(db: Database) -> None:
    """A skip must cost one SELECT, not an embedding-model load.

    This is the property that lets the command sit on a fifteen-minute timer
    without being noticeable. It is asserted rather than trusted because it is
    invisible in normal use - a needlessly-built embedder is slow, not wrong,
    and slow-not-wrong is what survives review unnoticed.
    """
    _idle_by(db, timedelta(seconds=0))
    built = {"n": 0}

    def factory():
        built["n"] += 1
        return None

    guarded_run(db.conn, embedder_factory=factory, idle_seconds=20 * 60)
    assert built["n"] == 0

    guarded_run(db.conn, embedder_factory=factory)
    assert built["n"] == 1
