"""Board tasks: claiming one, finishing one, and saying who holds one.

A task is claimed by exactly one agent - a compare-and-set, so two agents
racing for it cannot both win - and finished by its claimer or its author.

A claim records the claimer's persona, machine and hashed agent session, the
same identity a post records for its author. One persona runs several
sessions, and a claim that named only the persona could not tell a twin
session's claim from mine, so a task one session was working still looked like
new work to the others. ``done`` stays by persona: a session that died holding
a claim must not strand its task.
"""

from __future__ import annotations

import sqlite3

from . import grants
from .models import utcnow_iso

SESSION_CHARS = 8


def holder(post: dict) -> str:
    """Who holds a claimed task, as precisely as the store knows it."""
    out = str(post.get("claimed_by"))
    if post.get("claimed_origin"):
        out += f" on {post['claimed_origin']}"
    if post.get("claimed_agent"):
        out += f" (session {str(post['claimed_agent'])[:SESSION_CHARS]})"
    return out


def claim_nudge(post_id: str) -> str:
    """What an agent is told about an open task it may be about to work."""
    return (
        f'open task - claim it before you work it: board(action="claim", post_id="{post_id}"), '
        f"or every other session keeps seeing it as new"
    )


def _mine(found: dict, who: str, agent: str | None) -> bool:
    """Whether a claimed task is already this session's. An unknown session never is."""
    return found["claimed_by"] == who and agent is not None and found.get("claimed_agent") == agent


def claim(
    conn: sqlite3.Connection,
    post_id: str,
    who: str,
    *,
    origin: str | None = None,
    agent: str | None = None,
) -> dict:
    """Take an open task. Exactly one claimer wins; the rest are told who did.
    Claiming again from the session that holds it is a no-op, not an error."""
    from .board import BoardError, _require, _row

    found = _require(conn, post_id)
    if found["kind"] != "task":
        raise BoardError(f"post {post_id!r} is a {found['kind']}, not a task")
    grants.require_write_id(found["namespace_id"], "this namespace's board")
    cur = conn.execute(
        "UPDATE board_posts SET status = 'claimed', claimed_by = ?, claimed_origin = ?, "
        "claimed_agent = ?, updated_at = ? WHERE id = ? AND status = 'open'",
        (who, origin, agent, utcnow_iso(), post_id),
    )
    if cur.rowcount != 1:
        now = _require(conn, post_id)
        if now["status"] == "claimed" and _mine(now, who, agent):
            return _row(now)
        if now["status"] == "claimed":
            raise BoardError(f"task {post_id!r} is already claimed by {holder(now)}")
        raise BoardError(f"task {post_id!r} is {now['status']}")
    return _row(_require(conn, post_id))


def done(conn: sqlite3.Connection, post_id: str, who: str) -> dict:
    """Finish a post. Only its claimer or its author may."""
    from .board import BoardError, _require, _row

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
