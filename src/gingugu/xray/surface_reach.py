"""The surface edit note's "before": the checkout as it stood just before this edit.

The symbol cache is the graph as last written, which can predate a pull, a branch
switch or another session's edits. Diffing it whole against a fresh rebuild
reports every change since then as this edit's, on whatever file is edited next.
An edit changes one file, so the tree just before it is the fresh graph with that
file's own rows - its definitions, outgoing calls, sinks, entries and table uses -
taken from the cache instead. Drift elsewhere is then on both sides and cancels.
"""

from __future__ import annotations

from collections import deque

from .symbols import Symbols


def reach_of(adj: dict, starts) -> set:
    """Every node reachable from ``starts``, the starts included."""
    seen, queue = set(starts), deque(starts)
    while queue:
        for nxt in adj.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def as_before(cached: Symbols, after: Symbols, rel: str) -> Symbols:
    """``after`` with ``rel``'s rows as ``cached`` holds them."""
    defs = {p: qs for p, qs in after.defs.items() if p != rel}
    if rel in cached.defs:
        defs[rel] = cached.defs[rel]
    edges = {k: n for k, n in after.edges.items() if k[0] != rel}
    edges.update({k: n for k, n in cached.edges.items() if k[0] == rel})
    return Symbols(
        defs=defs,
        edges=edges,
        sinks=[s for s in after.sinks if s[0] != rel] + [s for s in cached.sinks if s[0] == rel],
        entries=[e for e in after.entries if e.path != rel]
        + [e for e in cached.entries if e.path == rel],
    )


def uses_before(cached: dict, after: dict, rel: str) -> dict:
    """The table uses (``table -> [(path, line)]``) with ``rel``'s as cached."""
    out: dict[str, list] = {}
    for uses, keep in ((after, lambda p: p != rel), (cached, lambda p: p == rel)):
        for table, locs in uses.items():
            out.setdefault(table, []).extend(loc for loc in locs if keep(loc[0]))
    return {t: locs for t, locs in out.items() if locs}
