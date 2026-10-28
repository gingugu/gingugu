"""The X-ray graph math: what the code leans on, and what a change reaches.

**Directed, unlike ``dream.centrality``.** That PageRank treats edges as
undirected because a memory relation's direction says nothing about which end
is load-bearing. An import's direction says everything: ``a`` importing ``b``
is ``a`` leaning on ``b``. Run undirected on gingugu's own import graph, it
ranked ``server`` second - imported by one module, importing 26. Here rank
flows from importer to imported, so a file ranks high when files that rank
high import it.

**Reachability is a separate fact from rank.** Blast radius is the full set of
transitive importers of the changed files, found by breadth-first search over
reversed edges so each carries its minimum hop count. Rank only orders that
set for reading; it never decides what is in it, and nothing is capped -
capping a blast radius would hide exactly the part worth seeing. Pagination
bounds the response, not the traversal.

**Certain vs candidate.** ``from pkg import name`` is a candidate edge when
``pkg/name.py`` exists: Python allows ``name`` to be either a submodule or a
name defined in the package, and static reading cannot always tell. A
dependent is ``certain`` only when a chain of certain edges reaches it.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

# Same constants as dream.centrality, for the same reason: they are the
# published algorithm's, and nothing here is a knob.
DAMPING = 0.85
MAX_ITERATIONS = 100
TOLERANCE = 1e-9


@dataclass(frozen=True)
class Link:
    """``src`` imports ``dst``."""

    src: str
    dst: str
    certain: bool = True
    # A parent package initializer that runs because a submodule was imported.
    # Reached by a change (blast keeps it), but not leaned on (rank skips it).
    implicit: bool = False


def _clean(nodes: Iterable[str], links: Iterable[Link]) -> list[Link]:
    known = set(nodes)
    return [ln for ln in links if ln.src != ln.dst and ln.src in known and ln.dst in known]


def pagerank(nodes: list[str], links: Iterable[Link]) -> dict[str, float]:
    """Directed PageRank, rank flowing importer -> imported. Deterministic.

    A file that imports nothing in the repo is dangling; its rank is spread
    uniformly rather than dropped, so the vector stays a distribution.
    """
    n = len(nodes)
    if n == 0:
        return {}
    out: dict[str, set[str]] = {node: set() for node in nodes}
    for ln in _clean(nodes, links):
        if not ln.implicit:
            out[ln.src].add(ln.dst)
    inbound: dict[str, list[str]] = {node: [] for node in nodes}
    for src, dsts in out.items():
        for dst in dsts:
            inbound[dst].append(src)

    uniform = 1.0 / n
    rank = dict.fromkeys(nodes, uniform)
    for _ in range(MAX_ITERATIONS):
        leaked = sum(rank[node] for node in nodes if not out[node])
        base = (1.0 - DAMPING) * uniform + DAMPING * leaked * uniform
        nxt = {
            node: base + DAMPING * sum(rank[src] / len(out[src]) for src in inbound[node])
            for node in nodes
        }
        delta = sum(abs(nxt[node] - rank[node]) for node in nodes)
        rank = nxt
        if delta < TOLERANCE:
            break
    return rank


def _reverse_bfs(seeds: list[str], importers: dict[str, list[tuple[str, bool]]], certain_only):
    hops = dict.fromkeys(seeds, 0)
    queue = deque(seeds)
    while queue:
        node = queue.popleft()
        for src, certain in importers.get(node, ()):
            if certain_only and not certain:
                continue
            if src not in hops:
                hops[src] = hops[node] + 1
                queue.append(src)
    return hops


def blast(
    seeds: list[str],
    files: dict[str, bool],
    links: Iterable[Link],
    ranks: dict[str, float],
    *,
    limit: int | None = None,
    offset: int = 0,
    hidden: set[str] | frozenset[str] = frozenset(),
) -> dict:
    """Every transitive importer of ``seeds``, split into code and candidate tests.

    ``files`` maps path -> is_test. Seeds are excluded from the result, even
    when a cycle leads back to them - except a test seed, which is itself a
    test to run (hops 0). ``limit``/``offset`` page each list; totals are
    always the whole set. ``hidden`` nodes (packages) carry the walk but are
    never listed: a change reaches a Go package's importers through it.
    """
    known = [s for s in dict.fromkeys(seeds) if s in files]
    unknown = [s for s in dict.fromkeys(seeds) if s not in files]
    importers: dict[str, list[tuple[str, bool]]] = {}
    for ln in _clean(files, links):
        importers.setdefault(ln.dst, []).append((ln.src, ln.certain))

    hops = _reverse_bfs(known, importers, certain_only=False)
    certain = _reverse_bfs(known, importers, certain_only=True)
    seed_set = set(known)

    def row(path: str) -> dict:
        return {
            "path": path,
            "hops": hops[path],
            "rank": round(ranks.get(path, 0.0), 8),
            "certain": path in certain,
        }

    reached = [p for p in hops if (p not in seed_set or files[p]) and p not in hidden]
    reached.sort(key=lambda p: (-ranks.get(p, 0.0), p))
    code = [p for p in reached if not files[p]]
    tests = [p for p in reached if files[p]]

    def page(paths: list[str]) -> list[dict]:
        end = None if limit is None else offset + limit
        return [row(p) for p in paths[offset:end]]

    return {
        "code": page(code),
        "tests": page(tests),
        "code_total": len(code),
        "tests_total": len(tests),
        "total": len(code) + len(tests),
        "unknown_seeds": unknown,
    }


def unreachable(
    files: dict[str, bool], links: Iterable[Link], *, roots: set[str]
) -> list[str] | None:
    """Files no import chain from a root reaches: dead-file candidates.

    None when there are no roots - with nothing to start from, every file is
    "unreachable" and the answer would say nothing.
    """
    roots = {r for r in roots if r in files}
    if not roots:
        return None
    forward: dict[str, list[str]] = {}
    for ln in _clean(files, links):
        forward.setdefault(ln.src, []).append(ln.dst)
    seen = set(roots)
    queue = deque(roots)
    while queue:
        for dst in forward.get(queue.popleft(), ()):
            if dst not in seen:
                seen.add(dst)
                queue.append(dst)
    return sorted(p for p in files if p not in seen)
