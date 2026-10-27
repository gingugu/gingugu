"""The agent board: posts agents leave for each other, and who has read them.

A post is addressed to a persona or to ``*`` (everyone), lives in a namespace,
and is either a ``message`` or a ``task``. A task is claimed by exactly one
agent - a compare-and-set, so two agents racing for it cannot both win - and
finished by its claimer or its author.

The fence is the namespace's, through the same chokepoints as memories:
reading a namespace sees its board, writing one posts to it. A post outside
the caller's grant reads as missing, never as forbidden, so its existence is
not disclosed.

This module is the store. ``handlers.board`` is the MCP surface, and is where
the author is derived - it is never something a caller gets to claim.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from . import grants
from .models import utcnow_iso

BROADCAST = "*"
KINDS = ("message", "task")
MAX_BODY = 16_000
MAX_LIMIT = 200
ROUTE_GRACE_SECONDS = 3600  # a routed reply reaches every session after this long
_PERSONA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_URI_PREFIX = "gingugu://board/inbox/"

_SELECT = (
    "SELECT p.id, p.parent_id, n.name AS namespace, p.namespace_id, p.author, p.author_session, "
    "p.author_origin, p.author_agent, p.recipient, p.recipient_agent, p.kind, p.status, "
    "p.claimed_by, p.body, p.created_at, p.updated_at "
    "FROM board_posts p JOIN namespaces n ON n.id = p.namespace_id"
)


class BoardError(ValueError):
    """A board request that cannot be honoured; the message is safe to return."""


def inbox_uri(persona: str) -> str:
    """The subscribable resource for ``persona``'s inbox (``*`` for broadcasts)."""
    return f"{_URI_PREFIX}{persona}"


def is_persona(name: str) -> bool:
    return name == BROADCAST or bool(_PERSONA_RE.fullmatch(name))


def touched_uris(post: dict) -> list[str]:
    """The inbox resources a new or changed ``post`` makes stale."""
    return [inbox_uri(post["recipient"])]


def _row(row: sqlite3.Row) -> dict:
    out = dict(row)
    out.pop("namespace_id", None)
    return out


def _fetch(conn: sqlite3.Connection, post_id: str) -> dict | None:
    """The post if the caller may read it; None reads the same as missing."""
    row = conn.execute(f"{_SELECT} WHERE p.id = ?", (post_id,)).fetchone()
    if row is None or not grants.can_read_id(row["namespace_id"]):
        return None
    return dict(row)


def _require(conn: sqlite3.Connection, post_id: str) -> dict:
    found = _fetch(conn, post_id)
    if found is None:
        raise BoardError(f"post {post_id!r} not found")
    return found


def is_me(author: str, origin: str | None, me: str, my_origin: str | None) -> bool:
    """Whether a post by ``author`` on ``origin`` is the caller's own.

    Same persona on a different machine is a twin, not me - but only when both
    machines are known; an unknown origin keeps the persona as the identity.
    """
    if author != me:
        return False
    return origin is None or my_origin is None or origin == my_origin


def _reply_to(
    conn: sqlite3.Connection, parent: dict, root_id: str, author: str, origin: str | None
) -> str:
    """Who a reply is for: the post's author, unless that is me - then whoever spoke last."""
    if not is_me(parent["author"], parent["author_origin"], author, origin):
        return parent["author"]
    rows = conn.execute(
        "SELECT author, author_origin FROM board_posts WHERE id = ? OR parent_id = ? "
        "ORDER BY created_at DESC, rowid DESC",
        (root_id, root_id),
    ).fetchall()
    for row in rows:
        if not is_me(row["author"], row["author_origin"], author, origin):
            return row["author"]
    return _require(conn, root_id)["recipient"]  # nobody has answered: nudge whoever I asked


def _route(
    conn: sqlite3.Connection, parent: dict, root_id: str, recipient: str, me: str, origin
) -> str | None:
    """The agent session a reply to ``recipient`` is for, or None (unrouted).

    The post being answered, when the recipient wrote it - not their latest
    post in the thread, which may be another session's. Otherwise (``to``
    named someone else, or I am nudging my own thread) their latest post that
    is not mine, the same row ``_reply_to`` picks.
    """
    if recipient == BROADCAST:
        return None
    if parent["author"] == recipient and not is_me(
        parent["author"], parent["author_origin"], me, origin
    ):
        return parent["author_agent"]
    rows = conn.execute(
        "SELECT author, author_origin, author_agent FROM board_posts "
        "WHERE author = ? AND (id = ? OR parent_id = ?) ORDER BY created_at DESC, rowid DESC",
        (recipient, root_id, root_id),
    ).fetchall()
    for row in rows:
        if not is_me(row["author"], row["author_origin"], me, origin):
            return row["author_agent"]
    return None


def post(
    conn: sqlite3.Connection,
    *,
    namespace_id: str,
    author: str,
    body: str,
    recipient: str | None = None,
    kind: str = "message",
    parent_id: str | None = None,
    author_session: str | None = None,
    author_origin: str | None = None,
    author_agent: str | None = None,
) -> dict:
    """Leave a post; a reply joins its parent's thread, namespace and author."""
    if kind not in KINDS:
        raise BoardError(f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(body, str) or not body.strip():
        raise BoardError("body must not be empty")
    if len(body) > MAX_BODY:
        raise BoardError(f"body is over {MAX_BODY} characters")
    parent = None
    if parent_id is not None:
        parent = _require(conn, parent_id)
        parent_id = parent["parent_id"] or parent["id"]  # threads are one level deep
        namespace_id = parent["namespace_id"]
        recipient = recipient or _reply_to(conn, parent, parent_id, author, author_origin)
    recipient = recipient or BROADCAST
    if not is_persona(recipient):
        raise BoardError(f"recipient {recipient!r} is not a persona name or '*'")
    route = _route(conn, parent, parent_id, recipient, author, author_origin) if parent else None
    grants.require_write_id(namespace_id, "this namespace's board")
    now = utcnow_iso()
    post_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO board_posts(id, namespace_id, parent_id, author, author_session, "
        "author_origin, author_agent, recipient, recipient_agent, kind, status, claimed_by, "
        "body, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL, ?, ?, ?)",
        (
            post_id,
            namespace_id,
            parent_id,
            author,
            author_session,
            author_origin,
            author_agent,
            recipient,
            route,
            kind,
            body,
            now,
            now,
        ),
    )
    return _row(_require(conn, post_id))


def _inbox_where(
    reader: str, include_read: bool, origin: str | None = None, agent: str | None = None
) -> tuple[str, list[object]]:
    # My own persona's post is mail only from a twin: both machines known, and not mine.
    clauses = [
        "p.recipient IN (?, ?)",
        "(p.author != ? OR (? IS NOT NULL AND p.author_origin IS NOT NULL "
        "AND p.author_origin != ?))",
        "p.status != 'done'",
        "(p.status = 'open' OR p.claimed_by = ?)",
        # A routed reply is for one session until it has sat unread for the grace.
        "(p.recipient_agent IS NULL OR ? IS NULL OR p.recipient_agent = ? OR p.created_at <= ?)",
    ]
    cutoff = (datetime.now(UTC) - timedelta(seconds=ROUTE_GRACE_SECONDS)).isoformat()
    params: list[object] = [reader, BROADCAST, reader, origin, origin, reader, agent, agent, cutoff]
    if not include_read:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM board_reads r WHERE r.post_id = p.id AND r.reader = ?)"
        )
        params.append(reader)
    scope, scope_params = grants.scope_clause("p.namespace_id")
    if scope is not None:
        clauses.append(scope)
        params.extend(scope_params)
    return " AND ".join(clauses), params


def inbox(
    conn: sqlite3.Connection,
    reader: str,
    *,
    include_read: bool = False,
    limit: int = 50,
    origin: str | None = None,
    agent: str | None = None,
) -> list[dict]:
    """What is waiting for ``reader`` (machine ``origin`` and agent session ``agent``,
    if known), oldest first."""
    where, params = _inbox_where(reader, include_read, origin, agent)
    rows = conn.execute(
        f"{_SELECT} WHERE {where} ORDER BY p.created_at, p.rowid LIMIT ?",
        [*params, min(max(1, int(limit)), MAX_LIMIT)],
    ).fetchall()
    return [_row(r) for r in rows]


def unread_count(
    conn: sqlite3.Connection, reader: str, origin: str | None = None, agent: str | None = None
) -> int:
    where, params = _inbox_where(reader, False, origin, agent)
    return conn.execute(f"SELECT count(*) FROM board_posts p WHERE {where}", params).fetchone()[0]


def thread(conn: sqlite3.Connection, post_id: str, reader: str) -> dict:
    """The thread ``post_id`` belongs to, root first; marks all of it read."""
    found = _require(conn, post_id)
    root = _require(conn, found["parent_id"]) if found["parent_id"] else found
    replies = conn.execute(
        f"{_SELECT} WHERE p.parent_id = ? ORDER BY p.created_at, p.rowid", (root["id"],)
    ).fetchall()
    now = utcnow_iso()
    conn.executemany(
        "INSERT OR IGNORE INTO board_reads(post_id, reader, read_at) VALUES (?, ?, ?)",
        [(pid, reader, now) for pid in [root["id"], *(r["id"] for r in replies)]],
    )
    return {"post": _row(root), "replies": [_row(r) for r in replies]}


def claim(conn: sqlite3.Connection, post_id: str, who: str) -> dict:
    """Take an open task. Exactly one claimer wins; the rest are told who did."""
    found = _require(conn, post_id)
    if found["kind"] != "task":
        raise BoardError(f"post {post_id!r} is a {found['kind']}, not a task")
    grants.require_write_id(found["namespace_id"], "this namespace's board")
    cur = conn.execute(
        "UPDATE board_posts SET status = 'claimed', claimed_by = ?, updated_at = ? "
        "WHERE id = ? AND status = 'open'",
        (who, utcnow_iso(), post_id),
    )
    if cur.rowcount != 1:
        now = _require(conn, post_id)
        if now["status"] == "claimed":
            raise BoardError(f"task {post_id!r} is already claimed by {now['claimed_by']}")
        raise BoardError(f"task {post_id!r} is {now['status']}")
    return _row(_require(conn, post_id))


def done(conn: sqlite3.Connection, post_id: str, who: str) -> dict:
    """Finish a post. Only its claimer or its author may."""
    found = _require(conn, post_id)
    grants.require_write_id(found["namespace_id"], "this namespace's board")
    if found["status"] == "done":
        raise BoardError(f"post {post_id!r} is already done")
    if who not in (found["author"], found["claimed_by"]):
        raise BoardError(f"only the author or the claimer may finish post {post_id!r}")
    conn.execute(
        "UPDATE board_posts SET status = 'done', updated_at = ? WHERE id = ? AND status != 'done'",
        (utcnow_iso(), post_id),
    )
    return _row(_require(conn, post_id))
