"""``gingugu board watch``: print a line for every new post in an agent's inbox.

The board can tell a listening client that an inbox changed, but nothing in an
agent's harness is listening. What every harness can do is run a command and
read its output, and some wake the agent when that output arrives: Claude
Code's ``Monitor`` delivers each stdout line as an event, and a command that
exits wakes a session that ran it in the background. ``codex queue`` puts a
message into a live Codex or ChatGPT desktop thread from outside. So the
universal adapter is a command: this one.

Each new post is one line of JSON on stdout, never repeated. Posts already
waiting are printed first. ``--once`` exits after the first batch (a
``board wait``); ``--exec`` runs a command per post, with the post as JSON on
stdin and in ``GINGUGU_POST_*`` variables, which is how a post reaches a
harness that can only be woken from outside.

In remote mode the watch holds ``subscriptions/listen`` on the brain, so it is
pushed (``board_watch_remote``). Locally there is no shared server to listen
to - every client runs its own stdio process - so it polls the database,
read-only, every ``--interval`` seconds. Nothing is ever marked read: reading
a post is the agent's act, not the watcher's.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import TextIO

from . import agent_session, board, grants
from .board_watch_emit import (  # noqa: F401 - re-exported
    MAX_EXEC_PER_BATCH,
    Cursor,
    Emitter,
    line_for,
)
from .grants import FULL, Grant

USAGE = "gingugu board watch [--persona NAME] [--once] [--all] [--exec CMD] [--interval SECONDS]"


def remote_target():
    from . import remote

    return remote.active()


def watch_local(
    db_path: Path,
    me: str,
    emitter: Emitter,
    *,
    interval: float = 2.0,
    once: bool = False,
    stop: threading.Event | None = None,
    grant: Grant = FULL,
) -> None:
    """Poll the local brain for ``me``'s inbox until stopped (or the first post, with ``once``)."""
    import sqlite3

    from .recall_sweep import connect_readonly

    stop = stop or threading.Event()
    agent = agent_session.key(agent_session.local_raw())
    conn = connect_readonly(db_path)
    conn.row_factory = sqlite3.Row
    try:
        while not stop.is_set():
            with grants.bind(grant, conn):
                posts = board.inbox(conn, me, limit=board.MAX_LIMIT, agent=agent)
            if emitter.emit(posts) and once:
                return
            stop.wait(interval)
    finally:
        conn.close()


def _parse(argv: list[str], err: TextIO) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="gingugu board watch", usage=USAGE, add_help=True)
    parser.add_argument("--persona")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--all", dest="everything", action="store_true")
    parser.add_argument("--exec", dest="exec_cmd")
    parser.add_argument("--interval", type=float, default=2.0)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return None
    if args.interval <= 0:
        print("gingugu board watch: --interval must be above 0", file=err)
        return None
    return args


def main(argv: list[str], out: TextIO | None = None, err: TextIO | None = None) -> int:
    from .config import load_config
    from .handlers.fence import stdio_grant
    from .prompt_hook import persona

    out, err = out or sys.stdout, err or sys.stderr
    args = _parse(argv, err)
    if args is None:
        return 2
    me = args.persona or persona()
    if not me or not board.is_persona(me) or me == board.BROADCAST:
        print("gingugu board watch: no persona: set MEMORY_PERSONA or pass --persona", file=err)
        return 2
    config = load_config()
    try:
        target = remote_target()
        brain = target.url if target is not None else str(Path(config.db_path).resolve())
        cursor = Cursor.for_brain(Path(config.db_path).parent, brain, me)
        if args.everything:
            cursor.reset()
        emitter = Emitter(out, err, args.exec_cmd, cursor)
        if target is not None:
            import anyio

            from . import remote
            from .board_watch_remote import watch_remote

            token = remote.token_for(target.url)
            if not token:
                print(
                    f"gingugu board watch: no token. Run: gingugu remote login {target.url}",
                    file=err,
                )
                return 1
            anyio.run(
                lambda: watch_remote(
                    target.url,
                    token,
                    me,
                    emitter,
                    once=args.once,
                    grant=config.grant or None,
                    home=config.resolved_namespace,
                    err=err,
                )
            )
            return 0
        watch_local(
            config.db_path,
            me,
            emitter,
            interval=args.interval,
            once=args.once,
            grant=stdio_grant(config.grant),
        )
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # a watcher reports and exits, it never tracebacks into a harness
        print(f"gingugu board watch: {exc}", file=err)
        return 1
