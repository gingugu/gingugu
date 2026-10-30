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


def _fit(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    kept: list[str] = []
    used = 0
    for line in text.split("\n"):
        if used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)[:max_chars] if kept else text[:max_chars]


def attach(body: object, cwd: str | None, max_chars: int) -> str | None:
    """The brief for the range ``body`` names, rendered from ``cwd``; None when
    there is nothing to say. Never raises."""
    try:
        ref = ref_in(body)
        if not enabled() or ref is None or not cwd:
            return None
        try:
            local.root(cwd)
        except (XrayError, ValueError, OSError):
            return None  # not a repo: stay silent
        try:
            data = brief.answer(cwd, ref, None)
        except (XrayError, ValueError):
            return f"X-ray brief: {json.dumps(ref)} is not a range this checkout knows."
        head = f"X-ray brief for {json.dumps(ref)}, rendered on this machine from this checkout:"
        return _fit(head + "\n" + data["text"], max_chars)
    except Exception:  # noqa: BLE001 - a brief must never cost the mail
        return None
