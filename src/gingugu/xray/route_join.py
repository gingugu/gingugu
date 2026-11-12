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

import time
from collections import defaultdict
from typing import NamedTuple

from .links import model
from .links.join_index import _seg_eq, _segs

PREFIX = "{prefix}"  # surface_route: a prefix set at runtime, of unknown length
MAX_EDGES = 20_000
MAX_SERVES = 5_000  # routes read; past it a hostile repo buys nothing more
MAX_CALLS = 50_000
MAX_CANDIDATES = 256  # routes tried per call, after the shape index narrows them
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


class Overrun(Exception):
    """The join ran past its deadline: the map is partial, so it says nothing."""


class _Cand(NamedTuple):
    serve: Serve
    head: tuple[str, ...]
    tail: tuple[str, ...]


def _matches(segs: list[str], c: _Cand) -> bool:
    if not c.serve.partial:
        return _seg_eq(segs, list(c.tail))
    head, tail = list(c.head), list(c.tail)
    if len(segs) < len(head) + len(tail):
        return False
    return _seg_eq(segs[: len(head)], head) and _seg_eq(segs[len(segs) - len(tail) :], tail)


class _Index:
    """Served routes by shape, each split into segments once: a complete route by
    (length, last name), a partial one by its last name."""

    def __init__(self, serves) -> None:
        self.complete: dict[tuple[int, str], list[_Cand]] = defaultdict(list)
        self.partial: dict[str, list[_Cand]] = defaultdict(list)
        for s in list(s for s in serves if s is not None)[:MAX_SERVES]:
            tail = tuple(_segs(s.key))
            c = _Cand(s, tuple(_segs(s.head)) if s.partial else (), tail)
            if s.partial:
                self.partial[tail[-1]].append(c)
            else:
                self.complete[(len(tail), tail[-1])].append(c)

    def candidates(self, segs: list[str]) -> list[_Cand]:
        last = segs[-1]
        out = self.complete.get((len(segs), last), []) + self.partial.get(last, [])
        if last != model.WILD:  # a call's unknown last part matches only a parameter
            out = out + self.complete.get((len(segs), model.WILD), [])
            out = out + self.partial.get(model.WILD, [])
        return out[:MAX_CANDIDATES]


def _calls_in_order(calls) -> list[Call]:
    return sorted(set(calls), key=lambda c: (c.path, c.line, c.key))[:MAX_CALLS]


def _check(deadline: float | None, i: int) -> None:
    if deadline is not None and i % 64 == 0 and time.monotonic() > deadline:
        raise Overrun


def _names(s: Serve) -> int:
    return model.concrete(s.key) + (model.concrete(s.head) if s.head else 0)


def _near_dirs(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.split("/")[:-1], b.split("/")[:-1], strict=False):
        if x != y:
            break
        n += 1
    return n


def _resolve(c: Call, hits: list[Serve]) -> tuple[list[Serve], bool]:
    """The serves a call resolves to, and whether more than one file is left."""
    top = max((_names(s), not s.partial) for s in hits)
    best = [s for s in hits if (_names(s), not s.partial) == top]
    nearest = max(_near_dirs(c.path, s.path) for s in best)
    best = [s for s in best if _near_dirs(c.path, s.path) == nearest]
    return best, len({s.path for s in best}) > 1


def join(
    serves,
    calls,
    own_hosts=frozenset(),
    cap: int = MAX_EDGES,
    *,
    deadline: float | None = None,
) -> list[Edge]:
    """Every call's edges to the routes it reaches, in caller order. Raises
    ``Overrun`` past ``deadline`` (``time.monotonic``)."""
    ix = _Index(serves)
    out: list[Edge] = []
    for i, c in enumerate(_calls_in_order(calls)):
        _check(deadline, i)
        if not _wanted(c, own_hosts):
            continue
        segs = _segs(c.key)
        hits = [
            k.serve for k in ix.candidates(segs) if k.serve.path != c.path and _matches(segs, k)
        ]
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


def _prefixes(serves) -> set[tuple[str, ...]]:
    """Every run of leading names a served route starts with (a partial route's
    known head only), up to its first parameter."""
    out: set[tuple[str, ...]] = set()
    for s in list(s for s in serves if s is not None)[:MAX_SERVES]:
        names = _segs(s.head) if s.partial else _segs(s.key)
        for i, name in enumerate(names):
            if name == model.WILD:
                break
            out.add(tuple(names[: i + 1]))
    return out


def near(c: Call, serves, prefixes: set | None = None) -> int:
    """Leading names ``c`` shares with the closest served route; a parameter
    ends the run. Linear in the call's length, whatever the route count."""
    known = prefixes if prefixes is not None else _prefixes(serves)
    segs = _segs(c.key)
    n = 0
    while n < len(segs) and tuple(segs[: n + 1]) in known:
        n += 1
    return n


def unserved(
    serves, calls, edges, own_hosts=frozenset(), *, deadline: float | None = None
) -> list[Call]:
    """Calls no served route answers, though they share two leading names with
    one: a typo, or an endpoint renamed or removed on the other side. A call
    that is only the start of a served route is a base URL, not a call. Raises
    ``Overrun`` past ``deadline``."""
    joined = {e.call for e in edges}
    ix = _Index(serves)
    known = _prefixes(serves)
    out = []
    for i, c in enumerate(_calls_in_order(calls)):
        _check(deadline, i)
        if c in joined or not _wanted(c, own_hosts):
            continue
        segs = _segs(c.key)
        if any(_matches(segs, k) for k in ix.candidates(segs)):
            continue  # served, only in its own file: the route's own declaration
        n = near(c, serves, known)
        if n >= UNSERVED_NEAR and n < len(segs):
            out.append(c)
    return out
