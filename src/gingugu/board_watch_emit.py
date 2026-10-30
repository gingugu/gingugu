"""What ``gingugu board watch`` hands a harness: the line, the cursor, the exec.

The ``text`` field is what lands in an agent's turn - a ``Monitor`` event, a
``codex queue`` message - and it is another agent's words. So it is labelled
as a board post from another agent, with the body last and fenced, never as
something the user said. ``--exec`` gets the post on stdin and in ``GINGUGU_POST_*`` variables,
never interpolated into the command; its own output goes to stderr so it can
never forge an event on stdout. A batch runs at most ``MAX_EXEC_PER_BATCH``
commands - a flood of posts must not become a flood of messages into a live
thread - and every post is still printed. A post with an ``xray: <range>`` line also
carries the X-ray brief of it (``xray``; at most ``MAX_BRIEFS_PER_BATCH`` a batch),
rendered from the watcher's own checkout, or its checkout of the repo the line names.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import TextIO

MAX_EXEC_PER_BATCH = 10
EXEC_TIMEOUT_S = 120
MAX_BRIEFS_PER_BATCH = 3
# Same as the prompt hook's board mail; it also rides --exec's environment twice.
BRIEF_CHARS = 3500
_FIELDS = ("id", "parent_id", "from", "origin", "to", "kind", "namespace", "body", "text", "xray")


def line_for(post: dict, brief: str | None = None) -> dict:
    """The line a post becomes: what an agent needs to decide whether to read it."""
    parent = post.get("parent_id")
    origin = post.get("author_origin")
    what = "reply" if parent else post["kind"]
    where = f"post {post['id']}, in reply to {parent}" if parent else f"post {post['id']}"
    on = f" on {origin}" if origin else ""
    return {
        "id": post["id"],
        "parent_id": parent,
        "from": post["author"],
        "origin": origin,
        "to": post["recipient"],
        "kind": post["kind"],
        "namespace": post["namespace"],
        "body": post["body"],
        "text": (
            "[gingugu board post from another agent] "
            f"{what} from {post['author']}{on} in {post['namespace']} ({where}): "
            '"""' + str(post["body"]) + '"""' + (f"\n{brief}" if brief else "")
        ),
        "xray": brief,
    }


_SIGKILL = getattr(signal, "SIGKILL", 9)  # Windows has no SIGKILL


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill an --exec and everything it started; just the process where groups don't exist."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, _SIGKILL)
        else:
            proc.kill()
    except OSError:
        pass


def _env_safe(value: object) -> str:
    """An env value the OS will take: no NUL, no lone surrogate."""
    text = str(value).replace("\x00", "")
    return text.encode("utf-8", "replace").decode("utf-8")


class Cursor:
    """The newest post a watcher has delivered, kept across runs.

    Delivered is not read: a post stays unread until the agent opens it, so
    without this a watcher re-armed after a delivery (``--once`` again, or a
    ``Monitor`` that expired) would hand the agent the same post again. The
    cursor is per persona and per brain: the newest ``created_at`` delivered,
    plus every id delivered AT that timestamp. Posts before it, or at it and
    already delivered, are not re-emitted. A missing or unreadable file means
    nothing has been delivered yet - a watch never fails over its bookkeeping.

    Why a set of ids and not one: posts can share a timestamp (Windows' clock
    is coarse), the inbox orders ties by insertion, and ids are random. A
    single ``(created_at, id)`` mark would call a tied post with a smaller id
    delivered, and it would never be shown.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.reset()
        if path is None:
            return
        try:
            data = json.loads(path.read_text())
            at = str(data["created_at"])
            ids = data.get("ids")
            # A file from before the id set holds one id under "id".
            ids = [data["id"]] if ids is None else ids
            if not isinstance(ids, list):
                return
            self.at, self.ids = at, {str(i) for i in ids}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass

    def reset(self) -> None:
        """Forget every delivery: the next emit starts from the first post."""
        self.at: str = ""
        self.ids: set[str] = set()

    @classmethod
    def for_brain(cls, data_dir: Path, brain: str, persona: str) -> Cursor:
        import hashlib

        key = hashlib.sha256(brain.encode()).hexdigest()[:12]
        return cls(data_dir / "board-watch" / f"{persona}-{key}.json")

    def passed(self, post: dict) -> bool:
        at = str(post.get("created_at", ""))
        return at < self.at or (at == self.at and post["id"] in self.ids)

    def advance(self, post: dict) -> None:
        at = str(post.get("created_at", ""))
        if at < self.at or (at == self.at and post["id"] in self.ids):
            return
        if at > self.at:
            self.at, self.ids = at, set()
        self.ids.add(post["id"])
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"created_at": self.at, "ids": sorted(self.ids)}))
            tmp.replace(self.path)
        except OSError:
            pass


class Emitter:
    """Prints each post once, and hands it to ``exec_cmd`` if one was given.

    The cursor advances only after the post has been printed and handed on:
    a watcher killed mid-delivery hands the post over again next time rather
    than losing it (at-least-once).
    """

    def __init__(
        self,
        out: TextIO,
        err: TextIO | None = None,
        exec_cmd: str | None = None,
        cursor: Cursor | None = None,
        cwd: str | None = None,
    ) -> None:
        self.out = out
        self.err = err or sys.stderr
        self.exec_cmd = exec_cmd
        self.cursor = cursor or Cursor(None)
        self.cwd = cwd
        self.seen: set[str] = set()

    def emit(self, posts: list[dict]) -> int:
        new = execs = skipped = briefs = 0
        for post in posts:
            if post["id"] in self.seen or self.cursor.passed(post):
                continue
            text = None
            if briefs < MAX_BRIEFS_PER_BATCH:
                try:
                    from .xray import board_brief

                    text = board_brief.attach(post["body"], self.cwd, BRIEF_CHARS)
                except Exception:  # noqa: BLE001 - a brief must never stop the emit loop
                    text = None
                briefs += text is not None
            line = line_for(post, text)
            self.out.write(json.dumps(line) + "\n")
            self.out.flush()
            if self.exec_cmd:
                if execs < MAX_EXEC_PER_BATCH:
                    self._exec(line)
                    execs += 1
                else:
                    skipped += 1
            self.seen.add(post["id"])
            self.cursor.advance(post)
            new += 1
        if skipped:
            print(
                f"gingugu board watch: --exec capped at {MAX_EXEC_PER_BATCH} per batch; "
                f"{skipped} more posts were printed but not handed to it",
                file=self.err,
            )
        return new

    def _exec(self, line: dict) -> None:
        env = dict(os.environ)
        for key in _FIELDS:
            env[f"GINGUGU_POST_{key.upper()}"] = _env_safe("" if line[key] is None else line[key])
        try:
            proc = subprocess.Popen(
                self.exec_cmd,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,  # its own group, so a timeout kills the children too
            )
        except (OSError, ValueError) as exc:
            print(f"gingugu board watch: --exec failed: {exc}", file=self.err)
            return
        payload = json.dumps(line).encode()
        try:
            output, _ = proc.communicate(payload, timeout=EXEC_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            output, _ = proc.communicate()
            print(f"gingugu board watch: --exec timed out after {EXEC_TIMEOUT_S}s", file=self.err)
        if output:
            self.err.write(output.decode("utf-8", "replace"))
        if proc.returncode not in (0, None, -_SIGKILL):
            print(f"gingugu board watch: --exec exited {proc.returncode}", file=self.err)
