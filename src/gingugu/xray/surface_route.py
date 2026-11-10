"""X7d: what an entry point answers to - its HTTP method and route, or its tool,
resource or command name - shared by every language's walk, the cache check and
the renderer.

Every walk record for an entry carries two strings beside the handler:

- ``method``: ``""`` (any method, or not an HTTP entry), one of ``METHODS``, or
  several of them joined by ``|`` in ``METHODS`` order (``"GET|POST"``).
- ``route``: the route as the file spells it, with any prefix the same file
  gives the router joined in front (``APIRouter(prefix=...)``, ``@Controller``,
  ``r.Group(...)``); an MCP tool, prompt or resource's registered name or URI; a
  CLI command's name. ``""`` when the source gives no literal. ``PREFIX`` stands
  where the file sets a prefix this walk cannot read (``prefix=settings.API``).

Both are read from untrusted source and rendered into the agent's context, so
``clean`` keeps a literal only when it is short, printable ASCII and free of
quotes, backticks, backslashes and whitespace; anything else is ``""``.
"""

from __future__ import annotations

import re

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "WS")
MAX_ROUTE = 256
PREFIX = "{prefix}"  # a prefix set at runtime: the route is known only after it
_BAD = frozenset('`"\\')
# Go 1.22 ServeMux patterns: "GET /items/{id}", "POST example.com/x".
_GO_PATTERN = re.compile(r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) +(\S+)\Z")
# A verb as an entry's last name means that method; anything else is "".
_VERBS = {m.lower(): m for m in METHODS if m != "WS"} | {"websocket": "WS"}


def clean(raw: object) -> str:
    """``raw`` when it is a route-safe literal, else ``""``."""
    if not isinstance(raw, str) or not 0 < len(raw) <= MAX_ROUTE:
        return ""
    if any(not ("!" <= c <= "~") or c in _BAD for c in raw):
        return ""
    return raw


def is_route(v: object) -> bool:
    """What a cache may hold: ``""`` or a string ``clean`` keeps whole."""
    return v == "" or (isinstance(v, str) and clean(v) == v)


def method_of(label: str) -> str:
    """The method an entry's decorator or registrar names by its last part
    (``app.get`` -> GET, ``r.POST`` -> POST); ``""`` when it names none."""
    return _VERBS.get(label.rsplit(".", 1)[-1].lower(), "")


def methods(names: list[object]) -> str:
    """A ``methods=[...]`` list joined in ``METHODS`` order; ``""`` when any
    member is not a literal method (the set is then not known)."""
    found = set()
    for n in names:
        if not isinstance(n, str) or n.upper() not in METHODS:
            return ""
        found.add(n.upper())
    return "|".join(m for m in METHODS if m in found)


def is_method(v: object) -> bool:
    if v == "":
        return True
    if not isinstance(v, str):
        return False
    parts = v.split("|")
    return parts == [m for m in METHODS if m in parts] and len(set(parts)) == len(parts)


def go_pattern(raw: str) -> tuple[str, str]:
    """A Go ``ServeMux`` pattern split into (method, route); no method: ("", raw)."""
    m = _GO_PATTERN.match(raw)
    return (m.group(1), m.group(2)) if m else ("", raw)


def join(prefix: str, route: str) -> str:
    """``prefix`` in front of ``route`` with exactly one ``/`` between them.
    An empty side yields the other; a result ``clean`` refuses is ``""``."""
    if not prefix or not route:
        return clean(prefix or route)
    return clean(prefix.rstrip("/") + "/" + route.lstrip("/"))


def label(kind: str, method: str, route: str, fallback: str) -> str:
    """How an entry is named to the agent: ``POST /users``, ``tool get_weather``'s
    ``get_weather``, ``GET (route not literal)``, or ``fallback`` (the decorator)."""
    if route:
        return f"{method} {route}" if method else route
    if kind == "http":
        return f"{method or fallback} (route not literal)"
    return fallback
