"""X7b: the route map - which file calls which route this repo serves.

Nothing imports a route: a frontend's ``fetch('/api/users')`` and the handler
that answers it are invisible to each other in the import graph. The served
side is X7d's entry points (mounted to full URLs, test files left out); the
calling side is X23's route mentions (a URL's host with it). ``route_join``
does the matching.

Client side only: the brain is never asked. Both sides come from caches that
are already kept fresh: the symbol graph and the links index.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import NamedTuple

from . import hook_common, route_join
from .links import index, model
from .route_join import Call, Edge, Serve

DEFAULT_BUDGET_S = 120.0  # a deliberate read; hooks pass their own


class RouteMap(NamedTuple):
    serves: tuple[Serve, ...]
    calls: tuple[Call, ...]
    edges: tuple[Edge, ...]
    unserved: tuple[Call, ...]  # calls answered nowhere here, though they look like ours
    complete: bool = True  # False: the budget ran out before every file was read
    unread: int = 0


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_ROUTES", "on").lower() not in ("0", "off", "false")


def serves_of(sym) -> list[Serve]:
    """Served routes from a symbol graph's HTTP entries; a test's route serves nothing."""
    out = []
    for e in sym.entries:
        if e.kind != "http" or not e.route or model.is_test(e.path):
            continue
        s = route_join.serve(e.route, e.path, e.line, e.method)
        if s is not None:
            out.append(s)
    return out


def read(
    root: Path,
    state_dir: Path,
    *,
    budget_s: float | None = None,
    sym=None,
    session_id: str | None = None,
) -> RouteMap:
    """The route map of the checkout at ``root``. ``sym`` reuses a symbol graph
    the caller already holds; ``budget_s`` bounds the mention scan."""
    if sym is None:
        sym = hook_common.symbols(root, state_dir, session_id)
    serves = serves_of(sym)
    deadline = time.monotonic() + (DEFAULT_BUDGET_S if budget_s is None else budget_s)
    facts, left = index.scan_repo(
        root, state_dir, deadline, own=True, extract=index._default_extract
    )
    own_hosts = frozenset(f.key for f in facts.provided if f.kind == model.HOST)
    calls = [
        Call(f.key, f.path, f.line, f.detail) for f in facts.mentioned if f.kind == model.ROUTE
    ]
    edges = route_join.join(serves, calls, own_hosts)
    miss = route_join.unserved(serves, calls, edges, own_hosts)
    return RouteMap(tuple(serves), tuple(calls), tuple(edges), tuple(miss), not left, left)


def served_in(m: RouteMap, rel: str) -> list[Edge]:
    """Edges into the routes ``rel`` serves, caller order."""
    return [e for e in m.edges if e.serve.path == rel]


def calls_in(m: RouteMap, rel: str) -> list[Edge]:
    """Edges out of the calls ``rel`` makes."""
    return [e for e in m.edges if e.call.path == rel]


def uncalled(m: RouteMap) -> list[Serve]:
    """Served routes no file here calls: normal for a public API, worth a look
    for an internal one. One per (file, route), first line."""
    hit = {(e.serve.path, e.serve.head, e.serve.key) for e in m.edges}
    seen: dict[tuple[str, str, str], Serve] = {}
    for s in m.serves:
        k = (s.path, s.head, s.key)
        if k not in hit and (k not in seen or s.line < seen[k].line):
            seen[k] = s
    return sorted(seen.values(), key=lambda s: (s.path, s.line))
