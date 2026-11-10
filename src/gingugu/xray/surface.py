"""X7d: the attack-surface map - which entry points reach which sinks, and by what path.

Pure: a symbol graph (with the ``sinks`` and ``entries`` its linkers kept) in, a
map out. From each entry's start nodes it walks the call edges forward,
breadth first, so the path kept to every sink is a shortest one; an inline
callback also carries the sinks it calls itself (hop 0).

Database sinks come from X7c rather than from typing receivers: a line that
uses a table, joined to the definition enclosing it, is a ``db:<table>`` sink
there. Every ``conn.execute`` on an untyped connection is then still counted.

**Both bounds, honestly.** The call graph is a lower bound, so a missing path
is not proof that none exists; a listed path exists in the code, though the
static reading cannot say whether input actually flows along it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from . import surface_catalog as cat
from .symbols import MODULE, Symbols, enclosing
from .symbols_model import Entry, Node, Sink

DB = "db"
MAX_VIA = 6  # names of a path shown; the hop count is always whole


@dataclass(frozen=True)
class Reach:
    sink: str  # catalogue name, or "db:<table>"
    category: str
    path: str  # where the sink call sits
    symbol: str  # the definition that makes it
    line: int
    hops: int  # calls from the entry's handler; 0 is the handler itself
    via: tuple[str, ...]  # the quals walked, handler first


def category(name: str) -> str | None:
    return DB if name.startswith(DB + ":") else cat.category(name)


def db_sinks(sym: Symbols, uses: dict[str, list[tuple[str, int]]]) -> list[Sink]:
    """Each X7c table use as a sink of its innermost enclosing definition."""
    out: list[Sink] = []
    for table, locs in uses.items():
        for path, line in locs:
            quals = enclosing(sym, path, line, line)
            out.append((path, quals[-1] if quals else MODULE, f"{DB}:{table}", line))
    return out


def _adjacency(sym: Symbols) -> dict[Node, list[Node]]:
    adj: dict[Node, list[Node]] = {}
    for sp, sq, dp, dq in sorted(sym.edges):
        adj.setdefault((sp, sq), []).append((dp, dq))
    return adj


def _rank(r: Reach) -> tuple:
    return (cat.CATEGORIES.index(r.category), r.hops, r.path, r.line, r.sink)


def reach(entry: Entry, adj: dict, at: dict[Node, list[tuple[str, int]]]) -> list[Reach]:
    """Every sink ``entry`` reaches, most dangerous and nearest first."""
    out = [
        Reach(name, category(name), entry.path, entry.handler, line, 0, ())
        for name, line in entry.direct
    ]
    parent: dict[Node, Node | None] = {n: None for n in entry.nodes}
    # A hop is one call from the handler: an inline callback's start nodes are what
    # it calls, a named handler's is the handler itself.
    hops = {n: int(entry.inline) for n in entry.nodes}
    queue = deque(entry.nodes)
    while queue:
        node = queue.popleft()
        for nxt in adj.get(node, ()):
            if nxt not in hops:
                hops[nxt], parent[nxt] = hops[node] + 1, node
                queue.append(nxt)
    for node, n in hops.items():
        if node not in at:
            continue
        via, step = [], node
        while step is not None:
            via.append(step[1])
            step = parent[step]
        for name, line in at[node]:
            kind = category(name)
            if kind is not None:
                out.append(Reach(name, kind, node[0], node[1], line, n, tuple(reversed(via))))
    return sorted(set(out), key=_rank)


def build(sym: Symbols, extra: list[Sink] = (), *, skip=lambda path: False) -> dict:
    """The map: each entry (``skip`` drops test files) with what it reaches, plus the
    sinks no entry reaches. ``extra`` adds sinks found outside the call graph."""
    adj = _adjacency(sym)
    at: dict[Node, list[tuple[str, int]]] = {}
    sinks = [s for s in [*sym.sinks, *extra] if not skip(s[0])]
    for path, caller, name, line in sinks:
        at.setdefault((path, caller), []).append((name, line))
    rows, reached = [], set()
    for e in sorted(sym.entries, key=lambda e: (e.path, e.line, e.label, e.handler)):
        if skip(e.path):
            continue
        found = reach(e, adj, at)
        reached.update((r.path, r.symbol, r.sink, r.line) for r in found)
        rows.append((e, found))
    rows.sort(key=lambda row: (_rank(row[1][0]) if row[1] else (len(cat.CATEGORIES),), row[0].path))
    unreached = sorted(
        {s for s in sinks if (s[0], s[1], s[2], s[3]) not in reached},
        key=lambda s: (cat.CATEGORIES.index(category(s[2])), s[0], s[3]),
    )
    return {"entries": rows, "unreached": unreached, "sinks": len(sinks)}
