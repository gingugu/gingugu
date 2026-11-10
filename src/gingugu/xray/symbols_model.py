"""The symbol graph's shape and its cache form: definitions, call edges, and (X7d)
the attack-surface records the linkers keep beside them.

``sinks`` and ``entries`` are what ``surface_link`` collects while a linker runs:
a call that reaches a catalogued external sink (or runs a built query), and an
entry point with the nodes its handler starts from. Every name in them is
rendered into the agent's context, so ``from_json`` takes only names the
catalogue wrote, identifier chains and the brain's own path rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import surface_catalog as cat
from .symbols_walk import is_qual
from .validate import is_valid_path

_KINDS = ("function", "method", "class")

Def = tuple[str, int, int]  # (kind, line, end)
Key = tuple[str, str, str, str]  # (src path, src symbol, dst path, dst symbol)
Node = tuple[str, str]  # (path, qual)
Sink = tuple[str, str, str, int]  # (path, caller qual, catalogue name, line)


@dataclass(frozen=True)
class Entry:
    path: str
    label: str  # the decorator or registrar, as written
    kind: str  # surface_catalog.KINDS
    line: int
    handler: str  # the handler's qual; an inline callback's: the scope it sits in
    nodes: tuple[Node, ...]  # where its reach starts; empty when unresolved
    direct: tuple[tuple[str, int], ...] = ()  # sinks an inline callback calls itself
    inline: bool = False


@dataclass
class Symbols:
    defs: dict[str, dict[str, Def]] = field(default_factory=dict)
    edges: dict[Key, int] = field(default_factory=dict)  # -> call sites
    resolved: int = 0
    external: int = 0
    unresolved: int = 0
    sinks: list[Sink] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)


def to_json(sym: Symbols) -> dict:
    return {
        "defs": {p: {q: list(d) for q, d in qs.items()} for p, qs in sym.defs.items()},
        "edges": [[*k, n] for k, n in sym.edges.items()],
        "resolved": sym.resolved,
        "external": sym.external,
        "unresolved": sym.unresolved,
        "sinks": [list(s) for s in sym.sinks],
        "entries": [
            [e.path, e.label, e.kind, e.line, e.handler, [list(n) for n in e.nodes]]
            + [[list(d) for d in e.direct], e.inline]
            for e in sym.entries
        ],
    }


def _count(v: object, low: int = 0) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= low


def _is_def(d: object) -> bool:
    return isinstance(d, list) and len(d) == 3 and d[0] in _KINDS and _count(d[1]) and _count(d[2])


def _is_edge(e: object) -> bool:
    return (
        isinstance(e, list)
        and len(e) == 5
        and is_valid_path(e[0])
        and is_valid_path(e[2])
        and is_qual(e[1], module_ok=True)
        and is_qual(e[3])
        and _count(e[4], 1)
    )


def _is_sink(s: object) -> bool:
    return (
        isinstance(s, list)
        and len(s) == 4
        and is_valid_path(s[0])
        and is_qual(s[1], module_ok=True)
        and cat.category(s[2]) is not None
        and _count(s[3])
    )


def _is_node(n: object) -> bool:
    return isinstance(n, list) and len(n) == 2 and is_valid_path(n[0]) and is_qual(n[1], True)


def _is_direct(d: object) -> bool:
    return isinstance(d, list) and len(d) == 2 and cat.category(d[0]) is not None and _count(d[1])


def _is_entry(e: object) -> bool:
    if not (isinstance(e, list) and len(e) == 8 and isinstance(e[2], str)):
        return False
    path, label, kind, line, handler, nodes, direct, inline = e
    return (
        is_valid_path(path)
        and cat.is_label(label)
        and kind in cat.KINDS
        and _count(line)
        and is_qual(handler, module_ok=True)
        and isinstance(inline, bool)
        and isinstance(nodes, list)
        and all(_is_node(n) for n in nodes)
        and isinstance(direct, list)
        and all(_is_direct(d) for d in direct)
    )


def _entry(e: list) -> Entry:
    nodes = tuple((n[0], n[1]) for n in e[5])
    direct = tuple((d[0], d[1]) for d in e[6])
    return Entry(e[0], e[1], e[2], e[3], e[4], nodes, direct, e[7])


def from_json(data: object) -> Symbols | None:
    """The cache form back, or None for any shape ``to_json`` never writes (untrusted)."""
    keys = {"defs", "edges", "resolved", "external", "unresolved", "sinks", "entries"}
    if not isinstance(data, dict) or set(data) != keys:
        return None
    defs, edges, sinks, entries = data["defs"], data["edges"], data["sinks"], data["entries"]
    if not all(isinstance(v, list) for v in (edges, sinks, entries)) or not isinstance(defs, dict):
        return None
    if not all(_count(data[k]) for k in ("resolved", "external", "unresolved")):
        return None
    for path, qs in defs.items():
        if not is_valid_path(path):  # rendered to the agent: the brain's own path rule
            return None
        if not isinstance(qs, dict) or not all(is_qual(q) and _is_def(d) for q, d in qs.items()):
            return None
    if not (all(map(_is_edge, edges)) and all(map(_is_sink, sinks))):
        return None
    if not all(map(_is_entry, entries)):
        return None
    return Symbols(
        defs={p: {q: (d[0], d[1], d[2]) for q, d in qs.items()} for p, qs in defs.items()},
        edges={(e[0], e[1], e[2], e[3]): e[4] for e in edges},
        resolved=data["resolved"],
        external=data["external"],
        unresolved=data["unresolved"],
        sinks=[(s[0], s[1], s[2], s[3]) for s in sinks],
        entries=[_entry(e) for e in entries],
    )
