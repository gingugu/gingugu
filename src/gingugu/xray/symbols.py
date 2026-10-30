"""The resolution half of the symbol graph: walk records from every file in, call edges out.

``symbols_walk`` records what one file says; this applies those records to the
whole repo through the same ``ModuleIndex`` the import graph uses. A call
resolves only through what static reading can prove:

- a definition in an enclosing scope (class bodies excepted, as in Python: a
  method's bare ``check()`` never means the class's ``check``);
- an import binding, to a module's top-level definition or a class's method;
- a variable whose class is named in scope - ``s = Store()``, ``s: Store``, a
  typed argument, ``with Store() as s`` - in its own scope or one enclosing it;
- ``self``/``cls`` inside a class, to that class's own methods.

**A lower bound, by construction.** Every call is counted as resolved,
external (a builtin or a third-party module) or unresolved, and an unresolved
call is never guessed at: an inherited method or an untyped value stays
unresolved. So a symbol's callers here are callers that certainly exist, and
the file blast radius stays the upper bound.
"""

from __future__ import annotations

import builtins
from collections import deque
from dataclasses import dataclass, field

from .resolve import ModuleIndex, _from_base

MODULE = "<module>"
_BUILTINS = frozenset(dir(builtins))
_KINDS = ("function", "method", "class")
_EXT = "external"

Def = tuple[str, int, int]  # (kind, line, end)
Key = tuple[str, str, str, str]  # (src path, src symbol, dst path, dst symbol)


@dataclass
class Symbols:
    defs: dict[str, dict[str, Def]] = field(default_factory=dict)
    edges: dict[Key, int] = field(default_factory=dict)  # -> call sites
    resolved: int = 0
    external: int = 0
    unresolved: int = 0


class _Linker:
    def __init__(self, raws: dict[str, dict], idx: ModuleIndex) -> None:
        self.idx = idx
        self.out = Symbols(
            defs={rel: {d[0]: (d[1], d[2], d[3]) for d in raw["defs"]} for rel, raw in raws.items()}
        )
        self.raws = raws

    def _defs(self, path: str) -> dict[str, Def]:
        return self.out.defs.get(path, {})

    def _scopes(self, rel: str, caller: str) -> list[str]:
        """Where a name used in ``caller`` is looked up, innermost first. A class
        body above the caller is not a scope for it; the caller itself always is."""
        if caller == MODULE:
            return [""]
        defs, parts = self._defs(rel), caller.split(".")
        out = [caller]
        for i in range(len(parts) - 1, 0, -1):
            outer = ".".join(parts[:i])
            if defs.get(outer, ("class",))[0] != "class":
                out.append(outer)
        return out + [""]

    def _bound(self, rel: str, bind: list) -> tuple | str | None:
        """("mod", path) or ("sym", path, qual) for an import binding; _EXT or None."""
        idx = self.idx
        if bind[0] == "i":
            dotted = bind[2]
            if dotted.split(".")[0] not in idx.tops:
                return _EXT
            path = idx.names.get(dotted)
            return ("mod", path) if path else None
        level, module, name = bind[2], bind[3], bind[4]
        base = _from_base(level, module, rel, idx)
        if base is None:
            return None
        if level == 0 and base.split(".")[0] not in idx.tops:
            return _EXT
        sub = f"{base}.{name}" if base else name
        if sub in idx.names:
            return ("mod", idx.names[sub])
        home = idx.names.get(base)
        if home is not None and name in self._defs(home):
            return ("sym", home, name)
        return None

    def _name(self, rel: str, caller: str, name: str, binds: dict) -> tuple | str | None:
        defs = self._defs(rel)
        for scope in self._scopes(rel, caller):
            qual = f"{scope}.{name}" if scope else name
            if qual in defs:
                return ("sym", rel, qual)
        if name in binds:
            return self._bound(rel, binds[name])
        return _EXT if name in _BUILTINS else None

    def _member(self, target, attr: str) -> tuple | None:
        """``attr`` of a module (a top-level def) or of a class (a method)."""
        if not isinstance(target, tuple):
            return None
        if target[0] == "mod":
            return ("sym", target[1], attr) if attr in self._defs(target[1]) else None
        path, qual = target[1], target[2]
        if self._defs(path)[qual][0] != "class":
            return None
        member = f"{qual}.{attr}"
        return ("sym", path, member) if member in self._defs(path) else None

    def _class(self, rel: str, scope: str, rec: list, binds: dict) -> tuple | None:
        """The class a type record names, as ("sym", path, qual), else None."""
        form, x, y = rec[2], rec[3], rec[4]
        target = self._name(rel, scope, x, binds)
        if form == "a":
            target = self._member(target, y)
        if isinstance(target, tuple) and target[0] == "sym":
            if self._defs(target[1])[target[2]][0] == "class":
                return target
        return None

    def _typed(self, rel: str, caller: str, var: str, types: dict, binds: dict):
        """(found, class) for ``var`` in ``caller`` or a scope enclosing it."""
        for scope in self._scopes(rel, caller):
            rec = types.get((scope or MODULE, var))
            if rec is not None:
                return True, self._class(rel, scope or MODULE, rec, binds)
        return False, None

    def _call(self, rel: str, rec: list, types: dict, binds: dict) -> tuple | str | None:
        caller, form, x, y = rec[0], rec[1], rec[2], rec[3]
        if form == "s":
            qual = f"{x}.{y}"
            return ("sym", rel, qual) if qual in self._defs(rel) else None
        if form == "n":
            target = self._name(rel, caller, x, binds)
            return target if target == _EXT or (target and target[0] == "sym") else None
        found, cls = self._typed(rel, caller, x, types, binds)
        if found:  # a variable shadows any import of the same name
            return self._member(cls, y)
        target = self._name(rel, caller, x, binds)
        return target if target == _EXT else self._member(target, y)

    def link(self) -> Symbols:
        out = self.out
        for rel, raw in self.raws.items():
            binds = {b[1]: b for b in raw["binds"]}  # the last binding of a name wins
            types = {(t[0], t[1]): t for t in raw["types"]}
            for rec in raw["calls"]:
                target = self._call(rel, rec, types, binds)
                if target == _EXT:
                    out.external += 1
                elif target is None:
                    out.unresolved += 1
                else:
                    out.resolved += 1
                    key = (rel, rec[0], target[1], target[2])
                    if (key[0], key[1]) != (key[2], key[3]):
                        out.edges[key] = out.edges.get(key, 0) + 1
        return out


def link(raws: dict[str, dict], idx: ModuleIndex) -> Symbols:
    """Resolve every file's ``symbols_walk`` records (path -> raw) against ``idx``."""
    return _Linker(raws, idx).link()


def callers(sym: Symbols, path: str, qual: str, *, limit: int | None = None, offset: int = 0):
    """Who calls ``qual`` in ``path``, directly and transitively; None if unknown.

    ``limit``/``offset`` page the lists; totals and ``reach_files`` are whole."""
    found = sym.defs.get(path, {}).get(qual)
    if found is None:
        return None
    rev: dict[tuple[str, str], dict[tuple[str, str], int]] = {}
    for (sp, sq, dp, dq), sites in sym.edges.items():
        rev.setdefault((dp, dq), {})[(sp, sq)] = sites
    seed = (path, qual)
    hops = {seed: 0}
    queue = deque([seed])
    while queue:
        node = queue.popleft()
        for src in rev.get(node, ()):
            if src not in hops:
                hops[src] = hops[node] + 1
                queue.append(src)
    direct = sorted(rev.get(seed, {}).items())
    reach = sorted((n for n in hops if n != seed), key=lambda n: (hops[n], n))
    end = None if limit is None else offset + limit
    return {
        "path": path,
        "symbol": qual,
        "kind": found[0],
        "line": found[1],
        "end": found[2],
        "direct": [{"path": p, "symbol": q, "sites": s} for (p, q), s in direct[offset:end]],
        "direct_total": len(direct),
        "reach": [{"path": p, "symbol": q, "hops": hops[(p, q)]} for p, q in reach[offset:end]],
        "reach_total": len(reach),
        "reach_files": sorted({p for p, _ in reach}),
    }


def enclosing(sym: Symbols, path: str, start: int, end: int) -> list[str]:
    """The innermost definitions in ``path`` that lines ``start``-``end`` touch."""
    defs = sym.defs.get(path, {})
    hit = [q for q, (_, line, last) in defs.items() if line <= end and last >= start]
    inner = [q for q in hit if not any(o.startswith(q + ".") for o in hit)]
    return sorted(inner, key=lambda q: (defs[q][1], q))


def to_json(sym: Symbols) -> dict:
    return {
        "defs": {p: {q: list(d) for q, d in qs.items()} for p, qs in sym.defs.items()},
        "edges": [[*k, n] for k, n in sym.edges.items()],
        "resolved": sym.resolved,
        "external": sym.external,
        "unresolved": sym.unresolved,
    }


def _count(v: object, low: int = 0) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= low


def _is_def(d: object) -> bool:
    return isinstance(d, list) and len(d) == 3 and d[0] in _KINDS and _count(d[1]) and _count(d[2])


def from_json(data: object) -> Symbols | None:
    """The cache form back, or None for any shape ``to_json`` never writes (untrusted)."""
    keys = {"defs", "edges", "resolved", "external", "unresolved"}
    if not isinstance(data, dict) or set(data) != keys:
        return None
    defs, edges = data["defs"], data["edges"]
    if not isinstance(defs, dict) or not isinstance(edges, list):
        return None
    if not all(_count(data[k]) for k in ("resolved", "external", "unresolved")):
        return None
    for qs in defs.values():
        if not isinstance(qs, dict) or not all(
            isinstance(q, str) and _is_def(d) for q, d in qs.items()
        ):
            return None
    for e in edges:
        if not (
            isinstance(e, list)
            and len(e) == 5
            and all(isinstance(s, str) for s in e[:4])
            and _count(e[4], 1)
        ):
            return None
    return Symbols(
        defs={p: {q: (d[0], d[1], d[2]) for q, d in qs.items()} for p, qs in defs.items()},
        edges={(e[0], e[1], e[2], e[3]): e[4] for e in edges},
        resolved=data["resolved"],
        external=data["external"],
        unresolved=data["unresolved"],
    )
