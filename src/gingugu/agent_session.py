"""Which agent session is speaking, for routing board replies.

One persona can run several sessions on one machine, and the inbox is per
persona, so a reply meant for the session that asked used to reach all of
them. Claude Code exports its session id as ``CLAUDE_CODE_SESSION_ID``; a post
records a hash of it, and a reply is routed to the hash of the session that
asked. The raw id is never stored: it is a live handle to a transcript.

Over stdio the id is the process's environment. A proxy forwards it to the
brain in ``X-Gingugu-Agent-Session``, so the brain sees the same session it
would have seen locally. An id that is absent or malformed is no session, and
no session means no routing - the reader sees everything, as before.
"""

from __future__ import annotations

import hashlib
import os
import re

from . import inbound

ENV = "CLAUDE_CODE_SESSION_ID"
HEADER = "X-Gingugu-Agent-Session"
_RAW_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def key(raw: object) -> str | None:
    """The stored form of an agent session id, or None if it is not a usable id."""
    if not isinstance(raw, str) or not _RAW_RE.fullmatch(raw):
        return None
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def local_raw() -> str | None:
    """This process's own agent session id, if Claude Code set a usable one."""
    raw = os.environ.get(ENV)
    return raw if key(raw) is not None else None


def current() -> str | None:
    """The hashed agent session of the request in flight."""
    msg = inbound.current()
    request = msg.request if msg is not None else None
    if request is not None:
        return key(request.headers.get(HEADER))
    return key(local_raw())


def headers() -> dict[str, str]:
    """What a proxy adds to every request it sends the brain."""
    raw = local_raw()
    return {HEADER: raw} if raw else {}
