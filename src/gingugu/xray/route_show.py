"""X7b: how a served route is grouped, named and ordered, for the tool and the hooks.

Pure, and light on imports: the read hook loads this, so it must not reach for
``brief`` (which imports the read hook).
"""

from __future__ import annotations

from collections import defaultdict

from .route_join import Serve, caller_class

_CLASS = {"code": 0, "doc": 1, "test": 2}

Group = tuple[str, str, str]  # (path, head, key): one served route of one file


def caller_key(path: str, line: int) -> tuple:
    """Code callers first, then docs, then tests; then path, then line."""
    return (_CLASS.get(caller_class(path), 0), path, line)


def groups_of(serves) -> dict[Group, list[Serve]]:
    """Serves merged per (file, route): every method it is served with."""
    out: dict[Group, list[Serve]] = defaultdict(list)
    for s in serves:
        out[(s.path, s.head, s.key)].append(s)
    return out


def methods_of(group: list[Serve]) -> str:
    return "|".join(sorted({m for s in group for m in s.method.split("|") if m}))


def shape(s: Serve) -> str:
    return f"{s.head}/{{prefix}}{s.key}" if s.partial else s.key


def display(group: list[Serve]) -> str:
    """``GET|POST /api/users``: the route as the agent reads it."""
    methods = methods_of(group)
    return f"{methods} {shape(group[0])}" if methods else shape(group[0])


def line_of(group: list[Serve]) -> int:
    return min(s.line for s in group)
