"""``memory_promote``: hand-picked gold, by id, into the crew commons.

Where the copy lands depends on what this brain is:

- it holds the crew (an owner's brain, the Pi): a copy inside this database;
- it is a crew mate's local brain, linked by ``gingugu crew join``: a push up
  the link, on the link's scoped token, as ``memory_store`` calls the crew
  brain matches on the source id itself (see ``promotion.receive``);
- neither: an error that says so - promoting never invents a crew namespace.

``store_promoted`` is the receiving end of that push, reached from
``memory_store`` whenever a payload carries a ``promoted_from`` stamp, so an
older ``gingugu promote`` CLI gets the same matching for free.
"""

from __future__ import annotations

import json
import logging

from .. import grants, promotion
from ..relations import RelationManager
from . import ServerContext
from .helpers import _err, _split_csv

logger = logging.getLogger(__name__)


def caller(ctx: ServerContext) -> str:
    """Who is promoting: the persona on stdio, the token's name over HTTP."""
    from .board import author_for
    from .fence import request_grant

    return author_for(ctx.transport, request_grant(ctx.transport, ctx.stdio_grant))


def _crew_link(ctx: ServerContext):
    """The (target, token) of this machine's crew link, or None.

    Only a stdio server with its own local brain pushes up a link; a served
    brain is the destination, never a relay.
    """
    if ctx.transport != "stdio":
        return None
    from .. import crew

    target = crew.active()
    if target is None:
        return None
    token = crew.token(target)
    if not token:
        raise ValueError(f"no token for {target.url} in the keychain; rerun `gingugu crew join`")
    return target, token


def _pick(ctx: ServerContext, ids: list[str], me: str):
    """Split the requested ids into (gold, held, missing)."""
    names = {n.id: n.name for n in ctx.namespaces.list()}
    gold, held, missing = [], [], []
    for memory_id in ids:
        mem = ctx.store.get(memory_id, record_access=False)
        if mem is None:
            missing.append(memory_id)
            continue
        ns = names.get(mem.namespace_id, "")
        if ns == promotion.CREW:
            reason = "already in crew"
        elif me and ns.lower() == me.lower():
            reason = "your persona namespace stays home"
        else:
            reason = promotion.hold_reason(
                confidence=mem.confidence.value,
                tags=mem.tags or [],
                title=mem.title,
                content=mem.content,
                about=mem.about,
                held=frozenset({me.lower()} if me else ()),
            )
        if reason:
            held.append({"id": mem.id, "title": mem.title, "reason": reason})
        else:
            gold.append((mem, ns))
    return gold, held, missing


def _own_metadata(raw: str | None) -> dict:
    """The source's metadata minus any stamp of its own; {} when there is none."""
    try:
        meta = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return {k: v for k, v in meta.items() if k != "promoted_from"} if isinstance(meta, dict) else {}


def _item(mem, ns: str, me: str) -> promotion.Promotion:
    return promotion.Promotion(
        title=mem.title,
        content=mem.content,
        type=mem.type,
        tags=[t for t in (mem.tags or []) if t != promotion.PROMOTED_TAG],
        about=mem.about,
        provenance=mem.provenance,
        stamp={
            **_own_metadata(mem.metadata),
            **promotion.stamp(source_id=mem.id, namespace=ns, contributor=me),
        },
    )


async def _push(target, token, items: list[promotion.Promotion]) -> list[dict]:
    from ..promote import _call, _session

    out = []
    async with _session(f"{target.url}/mcp", token) as s:
        for it in items:
            args = {
                "title": it.title,
                "content": it.content,
                "type": it.type.value,
                "confidence": "verified",
                "namespace": promotion.CREW,
                "tags": ",".join(it.tags),
                "metadata": json.dumps(it.stamp),
                "dedupe_check": False,
                "relation_check": False,
            }
            if it.about:
                args["about"] = it.about
            if it.provenance:
                args["provenance"] = it.provenance.value
            got = await _call(s, "memory_store", args)
            if not got.get("ok"):
                raise RuntimeError(f"the crew brain refused {it.title!r}: {got.get('error')}")
            out.append(got.get("promotion") or {"status": "promoted", "id": got["memory"]["id"]})
    return out


def store_promoted(
    ctx: ServerContext, ns, *, title, content, mem_type, conf, tags, about, prov, meta
):
    """``memory_store``'s path for a stamped payload: match, then land it.

    The net runs here too: a stamp is not a way around it. A promoted copy is
    ``verified`` by construction, so the payload must be.
    """
    me = caller(ctx)
    reason = promotion.hold_reason(
        confidence=conf.value,
        tags=tags,
        title=title,
        content=content,
        about=about,
        held=frozenset({me.lower()}),
    )
    if reason:
        return _err(f"promotion refused: {reason}")
    stamp = dict(promotion.source_of(meta) or {})
    stamp["contributor"] = me  # the server says who is calling, not the stamp
    got = promotion.receive(
        ctx.conn,
        ctx.store,
        RelationManager(ctx.conn),
        namespace_id=ns.id,
        item=promotion.Promotion(
            title=title,
            content=content,
            type=mem_type,
            tags=tags,
            about=about,
            provenance=prov,
            stamp={**_own_metadata(meta), "promoted_from": stamp},
        ),
    )
    mem = got.pop("memory", None) or ctx.store.get(got["id"], record_access=False)
    from .capability_view import summary_with_capability

    return {
        "ok": True,
        "memory": summary_with_capability(ctx, mem),
        "namespace": ns.name,
        "promotion": got,
    }


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool()
    async def memory_promote(ids: str, dry_run: bool = False) -> dict:
        """Promote hand-picked memories, by id, into the crew commons - the
        ``crew`` namespace every agent on the crew loads at session start.

        Promote what would have saved a crewmate an hour: a bug's real root
        cause, a tool or platform gotcha with its fix, a technique worth
        reusing, a decision whose reason outlives the project. Keep home:
        session notes, reflections, anything personal to the user, anything
        true for a day, every secret.

        ``ids`` is comma-separated. You pick; the filter is a net, not the
        judge - it still holds back anything unverified, tagged as session or
        personal (or with your own name), secret-shaped, already in crew, or
        from your own persona namespace, and says why under ``held``.
        ``missing`` lists ids that were not found.

        A copy is matched to its source by the source's id: promoting an
        unchanged memory again is ``unchanged``; promoting it after an edit is
        ``updated`` - the new copy supersedes the old, which is deprecated.
        ``dry_run=True`` reports ``would_promote`` and writes nothing.

        On a brain that holds the crew, the copy lands here. On a crew mate's
        local brain, it goes up the crew link. Anywhere else this is an error."""
        try:
            wanted = list(dict.fromkeys(_split_csv(ids)))
            if not wanted:
                return _err("ids is required: one or more memory ids, comma-separated")
            link = _crew_link(ctx)
            crew_ns = ctx.namespaces.get(promotion.CREW)
            if link is None and crew_ns is None:
                return _err(
                    "this brain holds no crew and is not linked to one; "
                    "`gingugu crew join` links a crew mate"
                )
            if link is not None and grants.current() is not None:
                # The push rides the link's own token, past this server's
                # fence: a narrowed (minion) server must not reach it.
                return _err("memory_promote is not available to a fenced server")
            me = caller(ctx)
            gold, held, missing = _pick(ctx, wanted, me)
            items = [_item(mem, ns, me) for mem, ns in gold]
            if dry_run:
                results = [{"status": "would_promote"} for _ in items]
            elif link is not None:
                results = await _push(*link, items)
            else:
                rel = RelationManager(ctx.conn)
                results = [
                    promotion.receive(ctx.conn, ctx.store, rel, namespace_id=crew_ns.id, item=it)
                    for it in items
                ]
            promoted = [
                {
                    "id": mem.id,
                    "title": mem.title,
                    "status": r["status"],
                    "copy_id": r.get("id"),
                    "superseded": r.get("superseded", []),
                }
                for (mem, _), r in zip(gold, results, strict=True)
            ]
            return {
                "ok": True,
                "target": link[0].url if link else promotion.CREW,
                "dry_run": dry_run,
                "promoted": promoted,
                "held": held,
                "missing": missing,
            }
        except Exception as exc:  # never crash the MCP loop
            logger.exception("memory_promote failed")
            return _err(f"memory_promote failed: {exc}")
