"""X20: a board post that names a range arrives with its X-ray brief.

The agents X19's brief is for fire no hooks, so a handoff should carry its brief
unasked. The post carries only the ref (a line of its own, ``xray: <range>``);
the brief is rendered on the receiver, from the receiver's own checkout, so no
paths or stale text cross the board. The ref is another agent's words: it passes
the same gate as any range before git sees it.
"""

from __future__ import annotations

import json
import os
import re

from . import brief, local
from .scan import XrayError

MAX_REF = 200
_MARKER = re.compile(r"^[ \t]*xray:", re.IGNORECASE | re.MULTILINE)
_LINE = re.compile(r"^[ \t]*xray:[ \t]*(\S+)[ \t]*$", re.IGNORECASE)


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_BOARD", "on").lower() not in ("0", "off", "false")


def ref_in(body: object) -> str | None:
    """The range a post names, or None. The first ``xray:`` line decides."""
    if not isinstance(body, str):
        return None
    found = _MARKER.search(body)
    if found is None:
        return None
    line = body[found.start() :].split("\n", 1)[0].rstrip("\r")
    m = _LINE.match(line)
    if m is None or len(m.group(1)) > MAX_REF:
        return None
    try:
        brief._check_range(m.group(1))
    except ValueError:
        return None
    return m.group(1)


CLIPPED = "(brief clipped to fit; code_xray brief or `gingugu xray brief` has all of it)"


def _fit(text: str, max_chars: int) -> str:
    """``text`` within ``max_chars``, cut on a line; a cut says so on its last line."""
    if len(text) <= max_chars:
        return text
    budget = max_chars - len(CLIPPED) - 1
    if budget <= 0:
        return text[:max_chars]
    kept: list[str] = []
    used = 0
    for line in text.split("\n"):
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    body = "\n".join(kept) if kept else text[:budget]
    return body + "\n" + CLIPPED


def _not_here(ref: str) -> str:
    """One line for a receiver with no checkout: a silent None would drop the ref."""
    return (
        f"X-ray brief: {json.dumps(ref)} is not available here (no git checkout); "
        f"run `gingugu xray brief --range {ref}` in that repo."
    )


def attach(
    body: object, cwd: str | None, max_chars: int, session_id: str | None = None
) -> str | None:
    """The brief for the range ``body`` names, rendered from ``cwd``; None when
    there is nothing to say. Never raises."""
    try:
        ref = ref_in(body)
        if not enabled() or ref is None:
            return None
        if not cwd:
            return _not_here(ref)
        try:
            local.root(cwd)
        except (XrayError, ValueError, OSError):
            return _not_here(ref)  # a watcher outside a repo, e.g. a Codex thread dir
        try:
            data = brief.answer(cwd, ref, None, session_id=session_id)
        except (XrayError, ValueError) as exc:
            why = json.dumps(str(exc)[:120])
            return f"X-ray brief: {json.dumps(ref)} is not available from this checkout ({why})."
        head = f"X-ray brief for {json.dumps(ref)}, rendered on this machine from this checkout:"
        return _fit(head + "\n" + data["text"], max_chars)
    except Exception:  # noqa: BLE001 - a brief must never cost the mail
        return None
