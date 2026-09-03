"""The proposal queue - everything the dream pass computes, waiting on a person.

The dream pass is allowed to run unattended because it cannot change the brain.
It computes structure over the relation graph and stages what it found here;
a human reads the queue and decides. That boundary is the reason the feature
is safe to put on a cron, and it only holds if nothing in this module ever
writes to ``memories``. It does not.

Three things live in the queue, and the split between them is the split between
what arithmetic can settle and what it cannot:

* ``edge`` - two memories that measure as close but have no relation between
  them. The pass proposes the **pair**; it never proposes the relation *type*,
  because choosing between ``supersedes`` and ``caused_by`` is a claim about
  meaning that no similarity score contains.
* ``cluster`` - a community of memories that link to each other more than to
  anything else. The pass proposes the **membership**; naming what the cluster
  is about is prose, and prose is judgment.
* ``core`` - a memory the graph itself ranks as central. The pass proposes the
  **rank**; whether "central" means "belongs in the identity tier" is a call
  about what matters, not about what is connected.

In every case the arithmetic hands over a structure and stops exactly where the
meaning begins.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from .models import utcnow_iso

# A staged proposal nobody has ruled on yet. Only these are re-computed by a
# later run; see ``stage``.
PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"

DECIDED = (ACCEPTED, REJECTED)

KINDS = ("edge", "cluster", "core")


def ordered_pair(a: str, b: str) -> tuple[str, str]:
    """Put an unordered pair in a stable order.

    An ``edge`` proposal is about two memories being close, and closeness has no
    direction - so (A, B) and (B, A) are the same finding. Without this the
    unique index would happily hold both, and the queue would show every
    reconnection twice with the sides swapped. Sorting by id is arbitrary but
    total, which is all the identity index needs.
    """
    return (a, b) if a <= b else (b, a)


class ProposalQueue:
    """Reads and writes the ``proposals`` table. Touches nothing else."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def stage(
        self,
        *,
        pass_name: str,
        kind: str,
        subject_id: str,
        score: float,
        evidence: dict,
        namespace_id: str | None = None,
        object_id: str | None = None,
    ) -> bool:
        """Stage one finding. Returns True if the row was written or refreshed.

        Re-running the pass must not turn the queue into a nagging machine, so
        an already-decided proposal is left exactly as it is and this returns
        False. A rejection is permanent: the whole point of recording it is that
        the same computation, run again tomorrow on the same graph, will reach
        the same conclusion, and the answer to that conclusion is already on
        file.

        A still-pending proposal *is* refreshed, because the score is a
        measurement of a graph that keeps changing underneath it. Showing a
        reviewer last week's number for a pair that has since drifted apart
        would be reporting a stale fact as a current one.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown proposal kind {kind!r}")
        if object_id is not None and object_id == subject_id:
            raise ValueError("a proposal cannot relate a memory to itself")

        now = utcnow_iso()
        cur = self._conn.execute(
            "INSERT INTO proposals("
            "  id, pass_name, kind, namespace_id, subject_id, object_id,"
            "  score, evidence, status, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, subject_id, COALESCE(object_id, '')) DO UPDATE SET "
            "  score = excluded.score,"
            "  evidence = excluded.evidence,"
            "  pass_name = excluded.pass_name "
            "WHERE proposals.status = ?",
            (
                str(uuid.uuid4()),
                pass_name,
                kind,
                namespace_id,
                subject_id,
                object_id,
                float(score),
                json.dumps(evidence, sort_keys=True),
                PENDING,
                now,
                PENDING,
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list(
        self,
        *,
        status: str | None = PENDING,
        kind: str | None = None,
        namespace_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Proposals, strongest first.

        Ordered by score descending because the queue is meant to be worked
        from the top and abandoned partway down: whatever a reviewer has time
        for should be the findings the arithmetic was most sure about. The id
        is the final tie-break so a run of equal scores has a stable order
        across calls rather than whatever the query planner felt like.
        """
        where: list[str] = []
        params: list = []
        if status:
            where.append("p.status = ?")
            params.append(status)
        if kind:
            where.append("p.kind = ?")
            params.append(kind)
        if namespace_id:
            where.append("p.namespace_id = ?")
            params.append(namespace_id)

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._conn.execute(
            "SELECT p.*, n.name AS namespace, "
            "       sm.title AS subject_title, om.title AS object_title "
            "FROM proposals p "
            "LEFT JOIN namespaces n ON n.id = p.namespace_id "
            "LEFT JOIN memories sm ON sm.id = p.subject_id "
            "LEFT JOIN memories om ON om.id = p.object_id "
            f"{clause} ORDER BY p.score DESC, p.id ASC LIMIT ?",
            (*params, max(1, limit)),
        ).fetchall()
        return [self._hydrate(row) for row in rows]

    def get(self, proposal_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT p.*, n.name AS namespace, "
            "       sm.title AS subject_title, om.title AS object_title "
            "FROM proposals p "
            "LEFT JOIN namespaces n ON n.id = p.namespace_id "
            "LEFT JOIN memories sm ON sm.id = p.subject_id "
            "LEFT JOIN memories om ON om.id = p.object_id "
            "WHERE p.id = ?",
            (proposal_id,),
        ).fetchone()
        return self._hydrate(row) if row else None

    def decide(self, proposal_id: str, status: str) -> dict:
        """Mark a proposal accepted or rejected. Idempotent per outcome.

        Deciding does not apply anything. Whatever an acceptance is *worth* -
        an edge, a tag, a pin - is written by the caller through the ordinary
        tools, with the judgment call that the pass deliberately left open
        supplied at that moment. Keeping the two apart means the queue can
        never become a side channel into the brain.
        """
        if status not in DECIDED:
            raise ValueError(f"status must be one of {DECIDED}, got {status!r}")
        existing = self.get(proposal_id)
        if existing is None:
            raise ValueError(f"proposal {proposal_id!r} not found")

        self._conn.execute(
            "UPDATE proposals SET status = ?, decided_at = ? WHERE id = ?",
            (status, utcnow_iso(), proposal_id),
        )
        self._conn.commit()
        return self.get(proposal_id) or existing

    def counts(self) -> dict:
        """Queue depth by status and by kind - the shape of the backlog."""
        by_status = {
            row["status"]: row["n"]
            for row in self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM proposals GROUP BY status"
            ).fetchall()
        }
        by_kind = {
            row["kind"]: row["n"]
            for row in self._conn.execute(
                "SELECT kind, COUNT(*) AS n FROM proposals " "WHERE status = ? GROUP BY kind",
                (PENDING,),
            ).fetchall()
        }
        return {
            "pending": by_status.get(PENDING, 0),
            "accepted": by_status.get(ACCEPTED, 0),
            "rejected": by_status.get(REJECTED, 0),
            "pending_by_kind": by_kind,
        }

    @staticmethod
    def _hydrate(row: sqlite3.Row) -> dict:
        out = dict(row)
        try:
            out["evidence"] = json.loads(out["evidence"])
        except (TypeError, ValueError):  # pragma: no cover - defensive
            out["evidence"] = {}
        return out
