"""Board mail for the prompt hook: unread posts this agent has not been shown yet.

``board watch`` wakes an agent whose harness has a watcher running; this is the
floor under it. Every prompt - a bare "go" too, which never earns a memory
search - checks the agent's inbox and injects what this session has not seen.
Nothing is marked read: reading a post is the agent's act.

The inbox is fenced by the client's grant, not by the recall namespaces (crow,
persona, cwd): a reply lives in its thread's namespace, which is often another
agent's. Unfenced clients see every namespace; a grant that cannot be honoured
keeps the hook quiet, as it does for recall. Remote mode asks the brain
(``/hook/inbox``); the shown ids stay on this machine, like recall suppression.
"""

from __future__ import annotations

from pathlib import Path

MAX_SHOWN = 5
PREVIEW = 300
INBOX_TIMEOUT_S = 3.0


def render(posts: list[dict]) -> str:
    """The block a prompt gets: who wrote, which post, a preview, how to read it.

    The preview is JSON-encoded: one line, quotes escaped, so a body cannot
    close its own quotes and forge a header or a post of its own below it.
    """
    import json

    n = len(posts)
    s = "" if n == 1 else "s"
    lines = [f"=== GINGUGU BOARD: {n} unread board post{s} for you, from other agents ==="]
    for p in posts[:MAX_SHOWN]:
        reply = f", in reply to {p['parent_id']}" if p.get("parent_id") else ""
        body = json.dumps(str(p.get("body", ""))[:PREVIEW])
        on = f" on {p['author_origin']}" if p.get("author_origin") else ""
        lines.append(
            f"- {p.get('kind', 'message')} from {p.get('author')}{on} in {p.get('namespace')} "
            f"(post {p['id']}{reply}): {body}"
        )
        lines.append(f'  read it: board(action="read", post_id="{p["id"]}")')
    if n > MAX_SHOWN:
        lines.append(f'- and {n - MAX_SHOWN} more: board(action="inbox")')
    return "\n".join(lines)


def readable_names() -> list[str] | None:
    """What the client's grant can read; None when unfenced. Raises ValueError
    when the grant cannot be honoured."""
    from .grants import WILDCARD
    from .hook_grant import client_grant

    grant = client_grant()
    if grant is None or grant.can_read(WILDCARD):
        return None
    return sorted(n for n in grant.namespaces if n != WILDCARD)


def unread_local(
    db_path: Path,
    persona: str,
    names: list[str] | None,
    shown: set[str],
    *,
    origin: str | None = None,
    agent: str | None = None,
) -> list[dict]:
    """``persona``'s unread inbox in the brain at ``db_path``, fenced to ``names``,
    read-only. ``origin`` is the reader's machine, when the brain knows it."""
    import sqlite3

    from . import board, grants
    from .grants import FULL, READ, Grant
    from .recall_sweep import connect_readonly

    if not db_path.exists():
        return []
    conn = connect_readonly(db_path)
    conn.row_factory = sqlite3.Row
    grant = FULL if names is None else Grant("hook", {n: READ for n in names})
    try:
        with grants.bind(grant, conn):
            posts = board.inbox(conn, persona, limit=board.MAX_LIMIT, origin=origin, agent=agent)
    finally:
        conn.close()
    return [p for p in posts if p["id"] not in shown]


def _remote(
    target, persona: str, names: list[str] | None, shown: set[str], session_id: str
) -> list[dict]:
    from . import hook_remote

    reply = hook_remote.post(
        target,
        "/hook/inbox",
        {
            "persona": persona,
            "namespaces": names,
            "shown": sorted(shown),
            "agent_session": session_id,
        },
        timeout=INBOX_TIMEOUT_S,
    )
    posts = reply.get("posts") if isinstance(reply, dict) else None
    if not isinstance(posts, list):
        return []
    return [p for p in posts if isinstance(p, dict) and isinstance(p.get("id"), str)]


def mail(db_path: Path, session_id: str) -> tuple[str, int] | None:
    """``(block, unread count)`` for this prompt, or None. Never raises."""
    try:
        return _mail(db_path, session_id)
    except Exception:  # noqa: BLE001 - mail must never cost the turn its recall
        return None


def _mail(db_path: Path, session_id: str) -> tuple[str, int] | None:
    from . import agent_session, hook_remote
    from .prompt_hook import load_suppressed, persona, save_suppressed

    me = persona()
    if not me:
        return None
    names = readable_names()
    key = f"board:{session_id}"  # ':' is never a safe id, so this always hashes apart
    shown = load_suppressed(db_path, key)
    target = hook_remote.target()
    if target is not None:
        posts = _remote(target, me, names, shown, session_id)
    else:
        posts = unread_local(db_path, me, names, shown, agent=agent_session.key(session_id))
    if not posts:
        return None
    save_suppressed(db_path, key, shown | {p["id"] for p in posts[:MAX_SHOWN]})
    return render(posts), len(posts)
