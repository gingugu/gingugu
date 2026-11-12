"""X7b: the route join - which call in this repo reaches which route it serves.

Pure: no disk, no git. ``route_map`` feeds it the served routes (X7d entries,
already mounted to full URLs) and the calls (X23 route mentions). The rules
are a router's, each pinned by a false edge measured on real repos:

- A route believed complete matches only a call of the same shape; a route
  under a runtime prefix (``{prefix}``) matches on its known head and tail.
- Most specific wins: the route with the most names, a complete one over a
  partial one; then, in a monorepo, the server nearest the caller. More than
  one server left is ambiguous, and every one is kept.
- A call inside an absolute URL to another host is not a call into this repo.
- A generic route (``/health``) or one with no name in it joins nothing.
"""

from __future__ import annotations

from collections import defaultdict
from typing import NamedTuple

from .links import model
from .links.join_index import _seg_eq, _segs

PREFIX = "{prefix}"  # surface_route: a prefix set at runtime, of unknown length
MAX_EDGES = 20_000
UNSERVED_NEAR = 2  # leading names a call must share with a served route to be 'missing'
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal")
_SPEC_WORDS = ("openapi", "swagger")


class Serve(NamedTuple):
    key: str  # normalized; for a partial route, the tail after the runtime prefix
    path: str
    line: int
    method: str
    head: str = ""  # a partial route's normalized part before the prefix; "" when none
    partial: bool = False


class Call(NamedTuple):
    key: str  # normalized route, as X23 mentions it
    path: str
    line: int
    host: str = ""  # the URL's host when the call is an absolute URL; "" when relative


class Edge(NamedTuple):
    call: Call
    serve: Serve
    methods: str  # every method that file serves the route with, "GET|POST"
    exact: bool  # a complete route; False: matched on a partial route's head and tail
    ambiguous: bool


def serve(route: str, path: str, line: int, method: str = "") -> Serve | None:
    """A served route from an X7d entry's URL, or None when too little of it is known."""
    if not route:
        return None
    if PREFIX not in route:
        key = model.norm_route(route)
        if not key or model.concrete(key) < 1:
            return None
        return Serve(key, path, line, method)
    head_raw = route.split(PREFIX, 1)[0]
    tail_raw = route.rsplit(PREFIX, 1)[1]
    head = model.norm_route(head_raw) if head_raw.strip("/") else ""
    tail = model.norm_route(tail_raw) if tail_raw else None
    if head is None or not tail or model.concrete(tail) < 1:
        return None
    if model.concrete(head) + model.concrete(tail) < 2:
        return None  # one name in all: it matches everything ending in it
    return Serve(tail, path, line, method, head if head != "/" else "", True)


def local_host(host: str, own_hosts=frozenset()) -> bool:
    """A call to this repo: relative, a bare service name, a local name, or a
    host this repo itself serves."""
    if not host or "." not in host:
        return True
    return host.endswith(_LOCAL_SUFFIXES) or host in own_hosts


def caller_class(path: str) -> str:
    """``code``, ``test`` or ``doc`` (an OpenAPI or Swagger spec is a doc)."""
    if model.is_test(path):
        return "test"
    if model.is_doc(path) or any(w in path.lower() for w in _SPEC_WORDS):
        return "doc"
    return "code"


def _wanted(c: Call, own_hosts) -> bool:
    return (
        c.key not in model.GENERIC_ROUTES
        and model.concrete(c.key) >= 1
        and local_host(c.host, own_hosts)
    )


def _matches(segs: list[str], s: Serve) -> bool:
    tail = _segs(s.key)
    if not s.partial:
        return _seg_eq(segs, tail)
    head = _segs(s.head)
    if len(segs) < len(head) + len(tail):
        return False
    return _seg_eq(segs[: len(head)], head) and _seg_eq(segs[len(segs) - len(tail) :], tail)


def _names(s: Serve) -> int:
    return model.concrete(s.key) + (model.concrete(s.head) if s.head else 0)


def _near_dirs(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.split("/")[:-1], b.split("/")[:-1], strict=False):
        if x != y:
            break
        n += 1
    return n


def _index(serves) -> dict[str, list[Serve]]:
    by_last: dict[str, list[Serve]] = defaultdict(list)
    for s in serves:
        if s is not None:
            by_last[_segs(s.key)[-1]].append(s)
    return by_last


def _resolve(c: Call, hits: list[Serve]) -> tuple[list[Serve], bool]:
    """The serves a call resolves to, and whether more than one file is left."""
    top = max((_names(s), not s.partial) for s in hits)
    best = [s for s in hits if (_names(s), not s.partial) == top]
    nearest = max(_near_dirs(c.path, s.path) for s in best)
    best = [s for s in best if _near_dirs(c.path, s.path) == nearest]
    return best, len({s.path for s in best}) > 1


def join(serves, calls, own_hosts=frozenset(), cap: int = MAX_EDGES) -> list[Edge]:
    """Every call's edges to the routes it reaches, in caller order."""
    by_last = _index(serves)
    out: list[Edge] = []
    for c in sorted(set(calls), key=lambda c: (c.path, c.line, c.key)):
        if not _wanted(c, own_hosts):
            continue
        segs = _segs(c.key)
        cands = by_last.get(segs[-1], [])
        if segs[-1] != model.WILD:  # a call's unknown last part matches only a parameter
            cands = cands + by_last.get(model.WILD, [])
        hits = [s for s in cands if s.path != c.path and _matches(segs, s)]
        if not hits:
            continue
        best, ambiguous = _resolve(c, hits)
        grouped: dict[tuple[str, str, str], list[Serve]] = defaultdict(list)
        for s in best:
            grouped[(s.path, s.head, s.key)].append(s)  # one route, every method it serves
        for group in sorted(grouped.values(), key=lambda g: (g[0].path, g[0].line)):
            methods = sorted({m for s in group for m in s.method.split("|") if m})
            first = min(group, key=lambda s: s.line)
            out.append(Edge(c, first, "|".join(methods), not first.partial, ambiguous))
            if len(out) >= cap:
                return out
    return out


def _known(serves) -> dict[str, list[list[str]]]:
    """Each route's known leading names, by its first one: only a route sharing
    a call's first name can share any."""
    out: dict[str, list[list[str]]] = defaultdict(list)
    for s in serves:
        if s is None:
            continue
        known = _segs(s.head) if s.partial else _segs(s.key)
        if known and known[0] != model.WILD:
            out[known[0]].append(known)
    return out


def near(c: Call, serves, known: dict | None = None) -> int:
    """Leading names ``c`` shares with the closest served route (a partial
    route's known head only); a parameter ends the run."""
    segs = _segs(c.key)
    if not segs:
        return 0
    known_by = known if known is not None else _known(serves)
    best = 0
    for names in known_by.get(segs[0], ()):
        n = 0
        for x, y in zip(segs, names, strict=False):
            if y == model.WILD or x != y:
                break
            n += 1
        best = max(best, n)
    return best


def unserved(serves, calls, edges, own_hosts=frozenset()) -> list[Call]:
    """Calls no served route answers, though they share two leading names with
    one: a typo, or an endpoint renamed or removed on the other side. A call
    that is only the start of a served route is a base URL, not a call."""
    joined = {e.call for e in edges}
    by_last = _index(serves)
    known = _known(serves)
    out = []
    for c in sorted(set(calls), key=lambda c: (c.path, c.line, c.key)):
        if c in joined or not _wanted(c, own_hosts):
            continue
        segs = _segs(c.key)
        cands = by_last.get(segs[-1], []) + by_last.get(model.WILD, [])
        if any(_matches(segs, s) for s in cands):
            continue  # served, only in its own file: the route's own declaration
        n = near(c, serves, known)
        if n >= UNSERVED_NEAR and n < len(_segs(c.key)):
            out.append(c)
    return out
