"""The dream-pass tool: run the passes, read the queue, decide on a finding.

Reading and deciding are one tool because they are one activity - a proposal is
meaningless without the number that produced it, and a reviewer who has to hold
an id in their head between two tools will stop reviewing.

**Accepting is where the judgment is supplied, and the tool insists on it.**
The passes deliberately stop short: an ``edge`` proposal has no relation type,
a ``cluster`` has no name. Accepting one therefore requires the caller to
provide the missing half, and an accept without it is refused rather than
guessed at. That refusal is the feature. If accepting an untyped pair silently
wrote ``related_to``, the arithmetic would have chosen a relation type after
all, by default, which is precisely what the design forbids.
"""

from __future__ import annotations

import logging

from ..dream_schedule import SKIPPED_LOCKED, guarded_run
from ..models import RelationType
from ..proposals import ACCEPTED, PENDING, REJECTED, ProposalQueue
from ..relations import RelationManager
from . import ServerContext
from .helpers import _err, _single_namespace_not_found

logger = logging.getLogger(__name__)

_ACTIONS = ("run", "list", "accept", "reject", "stats")


def register(mcp, ctx: ServerContext) -> None:
    queue = ProposalQueue(ctx.conn)
    relations = RelationManager(ctx.conn)

    @mcp.tool()
    def memory_dream(
        action: str = "list",
        namespace: str | None = None,
        proposal_id: str | None = None,
        kind: str | None = None,
        status: str | None = PENDING,
        relation_type: str | None = None,
        tag: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Deterministic background consolidation over the memory graph.

        The pass computes structure and stages it for review; it never writes to
        memories. Every finding is arithmetic a reader can recheck - PageRank over
        the relation graph, label propagation for communities, cosine similarity
        for orphan reconnection - and every one stops short of the judgment call
        it implies. Nothing enters the brain unattended.

        ``action``:

        * ``run`` - execute the passes and stage what they find. Safe to schedule;
          re-running refreshes pending scores and never resurfaces a proposal that
          was already decided. Scope with ``namespace``, or omit it to run over the
          whole store.
        * ``list`` - read the queue, strongest finding first. Filter by ``kind``
          (``edge``/``cluster``/``core``), ``status``, or ``namespace``.
        * ``accept`` - approve ``proposal_id`` **and apply it**. This is the step
          that supplies what the math could not: an ``edge`` needs
          ``relation_type``, a ``cluster`` needs ``tag`` (applied to every member),
          a ``core`` proposal pins the memory. Missing arguments are an error, not
          a default.
        * ``reject`` - decline ``proposal_id`` permanently. The row is kept so the
          same computation cannot raise it again on the next run.
        * ``stats`` - queue depth by status and kind.

        The three proposal kinds and what each leaves to you:

        * ``edge`` - two memories measure as close and nothing links them. You
          choose the relation type; a similarity score cannot tell whether one
          supersedes, caused, or contains the other.
        * ``cluster`` - a set of memories that link to each other more than to
          anything outside. You name it.
        * ``core`` - a memory the graph ranks as load-bearing. You decide whether
          structurally central means it belongs in the pinned identity tier.
        """
        if action not in _ACTIONS:
            return _err(f"unknown action {action!r}; expected one of {list(_ACTIONS)}")

        namespace_id = None
        if namespace:
            ns_name = ctx.namespaces.resolve_name(namespace)
            ns = ctx.namespaces.get(ns_name)
            if ns is None:
                return _single_namespace_not_found(namespace)
            namespace_id = ns.id

        try:
            if action == "run":
                # No idle gate: asking for a run in a session IS the intent,
                # and refusing it because the caller is demonstrably present
                # would be absurd. The lock still applies - a hand-run racing a
                # scheduled one wastes a core and muddles the log either way.
                report = guarded_run(
                    ctx.conn,
                    embedder_factory=lambda: ctx.store.embedder,
                    namespace_id=namespace_id,
                )
                if report["outcome"] == SKIPPED_LOCKED:
                    return _err(
                        "a scheduled dream pass is already running; try again when it finishes"
                    )
                return {"ok": True, "namespace": namespace, **report}

            if action == "list":
                return {
                    "ok": True,
                    "proposals": queue.list(
                        status=status,
                        kind=kind,
                        namespace_id=namespace_id,
                        limit=limit,
                    ),
                    "queue": queue.counts(),
                }

            if action == "stats":
                return {"ok": True, "queue": queue.counts()}

            if not proposal_id:
                return _err(f"action {action!r} requires proposal_id")
            proposal = queue.get(proposal_id)
            if proposal is None:
                return _err(f"proposal {proposal_id!r} not found")
            if proposal["status"] != PENDING:
                return _err(
                    f"proposal {proposal_id!r} was already {proposal['status']} "
                    f"at {proposal['decided_at']}"
                )

            if action == "reject":
                return {"ok": True, "proposal": queue.decide(proposal_id, REJECTED)}

            applied = _apply(ctx, relations, proposal, relation_type=relation_type, tag=tag)
            if not applied.get("ok", True):
                return applied
            return {
                "ok": True,
                "proposal": queue.decide(proposal_id, ACCEPTED),
                "applied": applied["applied"],
            }
        except ValueError as e:
            return _err(str(e))
        except Exception as e:  # pragma: no cover - the server must never crash
            logger.exception("memory_dream(%s) failed", action)
            return _err(f"{type(e).__name__}: {e}")


def _apply(ctx: ServerContext, relations, proposal: dict, *, relation_type, tag) -> dict:
    """Carry out what accepting a proposal means, per kind.

    The write happens through the ordinary managers - ``RelationManager``,
    ``MemoryStore`` - rather than from inside the queue. The queue owns its own
    table and nothing else, so there is no path by which a staged row can reach
    the brain without a person passing through here first.
    """
    kind = proposal["kind"]

    if kind == "edge":
        if not relation_type:
            return _err(
                "accepting an edge proposal requires relation_type. The pass measured "
                "that these two memories are close; it did not decide what the edge "
                "means. Prefer supersedes, contradicts, caused_by, parent_of/child_of; "
                "related_to is the fallback, not the default."
            )
        parsed = RelationType(relation_type)  # ValueError surfaces as a tool error
        relations.relate(
            source_id=proposal["subject_id"],
            target_id=proposal["object_id"],
            relation_type=parsed,
            metadata=None,
        )
        return {
            "ok": True,
            "applied": {
                "edge": f"{proposal['subject_id']} --{parsed.value}--> {proposal['object_id']}"
            },
        }

    if kind == "cluster":
        if not tag:
            return _err(
                "accepting a cluster proposal requires tag. The pass found which "
                "memories group together; naming the group is the part it cannot do."
            )
        members = proposal["evidence"].get("members") or []
        for member in members:
            ctx.store.add_tags(member, [tag])
        return {"ok": True, "applied": {"tagged": len(members), "tag": tag}}

    # core: the graph says this memory is load-bearing; accepting says it
    # belongs in the tier that always loads.
    updated = ctx.store.update(proposal["subject_id"], pinned=True)
    if updated is None:
        return _err(f"memory {proposal['subject_id']!r} not found")
    return {"ok": True, "applied": {"pinned": proposal["subject_id"]}}
