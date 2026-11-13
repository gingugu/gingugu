"""X7d: a Django ``re_path`` regex read as a route, only when it is one.

``route(raw)`` drops the anchors (one leading ``^``, one trailing ``$``), turns
``(?P<name>...)`` (no parenthesis inside) into ``{name}``, and unescapes ``\\.``
``\\-`` ``\\/``. Any other character a regex would read (``( ) [ ] { } ? * + | .
\\ ^ $``) makes the route ``""``: the pattern is not a literal route, and a guess
would be a made-up one. The result goes through ``surface_route.clean`` like every
route (the regex itself cannot: it holds backslashes), and one scan of the text
decides, so no input can make it recurse.
"""

from __future__ import annotations

import re

from . import surface_route as sr

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_UNESCAPE = frozenset(".-/")
_SPECIAL = frozenset("()[]{}?*+|.\\^$")
_MAX = 1024


def route(raw: str | None) -> str:
    """The route ``raw`` spells, or ``""`` when it is not one."""
    if raw is None or len(raw) > _MAX:
        return ""
    body = raw.removeprefix("^").removesuffix("$")
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if body.startswith("(?P<", i):
            end = body.find(")", i)
            gt = body.find(">", i)
            if end < 0 or gt < 0 or gt > end or "(" in body[i + 1 : end]:
                return ""
            if not _NAME.match(body[i + 4 : gt]):
                return ""
            out.append("{" + body[i + 4 : gt] + "}")
            i = end + 1
        elif c == "\\":
            if i + 1 >= len(body) or body[i + 1] not in _UNESCAPE:
                return ""
            out.append(body[i + 1])
            i += 2
        elif c in _SPECIAL:
            return ""
        else:
            out.append(c)
            i += 1
    return sr.clean("/" + "".join(out).lstrip("/"))
