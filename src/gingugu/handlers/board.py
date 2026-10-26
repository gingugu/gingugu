"""The ``board`` tool and the ``gingugu://board/inbox/{persona}`` resources.

The store lives in ``board``. This is the MCP surface over it, and the one
place the author of a post is decided: never by the caller, always by what the
server knows about the connection. Over HTTP that is the token's name (a
proxy's derived token is named for its persona); on stdio it is the process's
``MEMORY_PERSONA``, or ``minion`` for a warm minion's fenced server.

Every write commits, then emits a resource-updated event for the inbox it
changed, so a client holding ``subscriptions/listen`` on that inbox wakes.
"""

from __future__ import annotations

import json
import logging
import os

from mcp.shared.subscriptions import ResourceUpdated

from .. import board, board_delivery, grants
from ..grants import Grant, origin_name
from ..session import current_session_id
from . import ServerContext
from .helpers import _err

logger = logging.getLogger(__name__)

_ACTIONS = ("post", "inbox", "read", "reply", "claim", "done")
_DERIVED = "derived:"


def author_for(transport: str, grant: Grant | None) -> str:
    """Who is speaking on this connection."""
    if transport == "stdio":
        if os.environ.get("MEMORY_SERVER_ROLE", "").strip() == "minion":
            return "minion"
        from ..prompt_hook import persona

        return persona() or "local"
    name = grant.name if grant is not None else ""
    return name.removeprefix(_DERIVED) or "unknown"


def origin_for(transport: str, grant: Grant | None) -> str | None:
    """Which machine is speaking: a derived token's stamped origin, an owner
    token's own name. None on stdio and for a scoped token - unknown."""
    if transport == "stdio" or grant is None:
        return None
    if grant.derived:
        return origin_name(grant.origin)
    return origin_name(grant.name) if grant.is_full else None


def _caller(ctx: ServerContext) -> str:
    from .fence import request_grant

    return author_for(ctx.transport, request_grant(ctx.transport, ctx.stdio_grant))


def _origin(ctx: ServerContext) -> str | None:
    from .fence import request_grant

    return origin_for(ctx.transport, request_grant(ctx.transport, ctx.stdio_grant))


def unread_note(ctx: ServerContext) -> dict | None:
    """``{"unread": n}`` for ``memory_context`` when the caller is a persona, else None."""
    me = _caller(ctx)
    if me in ("local", "unknown", "minion", board.BROADCAST) or not board.is_persona(me):
        return None
    try:
        return {"unread": board.unread_count(ctx.conn, me, _origin(ctx))}
    except Exception:  # noqa: BLE001 - a count must never cost the session its context
        logger.exception("board unread count failed")
        return None


def _emit(ctx: ServerContext, post: dict) -> None:
    bus = getattr(ctx, "bus", None)
    if bus is None:
        return
    for uri in board.touched_uris(post):
        bus.emit(ResourceUpdated(uri=uri))


def register(mcp, ctx: ServerContext) -> None:
    @mcp.tool(name="board")
    def board_(
        action: str,
        body: str | None = None,
        to: str | None = None,
        kind: str = "message",
        post_id: str | None = None,
        namespace: str | None = None,
        include_read: bool = False,
        limit: int = 20,
    ) -> dict:
        """The agent board: leave posts for other agents sharing this brain, and read
        the ones left for you. Posts are not memories - they are addressed, read once,
        and never ranked. You post as yourself; the author is never an argument.

        Actions: "post" (body, optional to = a persona name or "*" for everyone,
        kind = "message" | "task", namespace), "inbox" (what is waiting for you:
        unread, addressed to you or everyone, not done; include_read to see read
        ones too), "read" (post_id: the whole thread, marked read), "reply"
        (post_id, body: joins that thread, addressed to that post's author - or,
        when that is you, to whoever last spoke in the thread - unless to is
        given), "claim" (post_id of an open task: exactly one agent wins), "done"
        (post_id: by its author or claimer).

        A post is seen only by readers of its namespace. A post or reply to a persona
        returns ``delivery``: ``visible`` true, false (no known client of theirs reads
        that namespace - they will not see it), or null (unverified).

        Your inbox is also the subscribable resource gingugu://board/inbox/<you>
        (gingugu://board/inbox/* for broadcasts)."""
        try:
            me, origin = _caller(ctx), _origin(ctx)
            if action == "inbox":
                posts = board.inbox(
                    ctx.conn, me, include_read=include_read, limit=limit, origin=origin
                )
                return {
                    "ok": True,
                    "me": me,
                    "unread": board.unread_count(ctx.conn, me, origin),
                    "posts": posts,
                }
            if action in ("read", "reply", "claim", "done") and not post_id:
                return _err(f"post_id is required for action {action!r}")
            if action == "read":
                out = board.thread(ctx.conn, post_id, me)
                ctx.conn.commit()
                return {"ok": True, **out}
            if action in ("post", "reply"):
                if not body:
                    return _err(f"body is required for action {action!r}")
                ns_id = None
                if action == "post":
                    # Created on first use, as memory_store does; under a grant
                    # that takes write on the name.
                    ns_id = ctx.namespaces.get_or_create(ctx.namespaces.resolve_name(namespace)).id
                made = board.post(
                    ctx.conn,
                    namespace_id=ns_id,
                    author=me,
                    body=body,
                    recipient=to,
                    kind=kind if action == "post" else "message",
                    parent_id=post_id if action == "reply" else None,
                    author_session=current_session_id(),
                    author_origin=origin,
                )
            elif action == "claim":
                made = board.claim(ctx.conn, post_id, me)
            elif action == "done":
                made = board.done(ctx.conn, post_id, me)
            else:
                return _err(f"unknown action {action!r}; valid actions: {', '.join(_ACTIONS)}")
            ctx.conn.commit()
            _emit(ctx, made)
            out = {"ok": True, "post": made}
            if action in ("post", "reply"):
                grants_seen = board_delivery.recipient_grants(
                    made["recipient"], ctx.derived, ctx.tokens
                )
                delivery = board_delivery.receipt(made["recipient"], made["namespace"], grants_seen)
                if delivery is not None:
                    out["delivery"] = delivery
            return out
        except (board.BoardError, grants.AccessDenied) as exc:
            ctx.conn.rollback()
            return _err(str(exc))
        except Exception as exc:
            ctx.conn.rollback()
            logger.exception("board failed")
            return _err(f"board failed: {exc}")

    @mcp.resource(board.inbox_uri("{persona}"), mime_type="application/json")
    async def board_inbox(persona: str) -> str:
        """What is waiting on the board for one persona: unread, not done."""
        from .fence import request_grant

        if not board.is_persona(persona):
            return json.dumps({"error": "not a persona name or '*'"})
        grant = request_grant(ctx.transport, ctx.stdio_grant)
        if grant is None:
            return json.dumps({"error": "unauthorized"})
        with grants.bind(grant, ctx.conn):
            posts = board.inbox(ctx.conn, persona, origin=origin_for(ctx.transport, grant))
        return json.dumps({"persona": persona, "posts": posts})
