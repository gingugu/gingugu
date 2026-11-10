"""The resolution half of the Go symbol graph: every file's records in, call edges out.

A Go package is a directory, so a name resolves across every file in it, and an
external ``_test`` package also sees the package it tests. A call resolves only
through what static reading can prove:

- a function, method or type defined anywhere in the caller's package;
- an import name, to the in-repo package's top-level definition;
- a value whose type is named in scope - its receiver, a parameter,
  ``var x T``, ``T{}``/``&T{}``, or the declared first result of the function
  that made it - to that type's method.

**A lower bound, by construction.** Every call is counted as resolved,
external (a builtin, or a package or type from outside every in-repo module) or
unresolved, never guessed: an interface method, a function value, a name two
files define (build tags), a value declared twice with different types, and a
type only an unmodelled expression names all stay unresolved.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from .. import surface_link
from ..symbols import Symbols
from ..symbols_walk import MODULE
from .go_symbols_value import value_resolver

_EXT = "external"
_BUILTINS = frozenset(
    "len cap append make new panic recover copy delete close print println min max clear "
    "complex real imag string int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 "
    "uintptr byte rune float32 float64 complex64 complex128 bool error any".split()
)
_VERSION = re.compile(r"v\d+")

Key = tuple[str, bool]  # (import path or "/dir", external-test package)
Ty = tuple[Key, str] | str | None  # a named type, _EXT, or unknown


def _default_name(path: str) -> str:
    """What an import is called when no alias and no in-repo clause says: the last
    segment, past a ``/vN`` major-version suffix."""
    parts = path.split("/")
    return parts[-2] if len(parts) > 1 and _VERSION.fullmatch(parts[-1]) else parts[-1]


class _Linker:
    def __init__(
        self, raws: dict[str, dict], placed: dict[str, str | None], inside: Callable[[str], bool]
    ) -> None:
        self.raws, self.inside = raws, inside
        self.out = Symbols(
            defs={rel: {d[0]: (d[1], d[2], d[3]) for d in r["defs"]} for rel, r in raws.items()}
        )
        self.key: dict[str, Key] = {}
        self.modless: set[str] = set()
        for rel, r in raws.items():
            test = rel.endswith("_test.go") and r["pkg"].endswith("_test")
            path = placed.get(rel)
            if path is None:  # no go.mod: the directory alone is the package
                self.modless.add(rel)
                path = "/" + rel.rpartition("/")[0]  # no import path starts with "/"
            self.key[rel] = (path, test)
        self.defs: dict[Key, dict[str, list[str]]] = {}
        self.pvars: dict[Key, dict[str, list[tuple[str, list]]]] = {}
        self.clause: dict[str, str] = {}
        self.by_path: dict[str, Key] = {}
        self.res: dict[str, dict[str, tuple[str, str]]] = {}
        self.local: dict[str, dict[tuple[str, str], list | None]] = {}
        self.imports: dict[str, dict[str, str]] = {}
        self._index()

    def _index(self) -> None:
        for rel, r in self.raws.items():
            k = self.key[rel]
            for qual in self.out.defs[rel]:
                self.defs.setdefault(k, {}).setdefault(qual, []).append(rel)
            counts: dict[str, int] = {}
            for q, *_ in r["results"]:
                counts[q] = counts.get(q, 0) + 1
            self.res[rel] = {q: (x, y) for q, x, y in r["results"] if counts[q] == 1}
            local: dict[tuple[str, str], list | None] = {}
            for t in r["types"]:
                if t[0] == MODULE:
                    self.pvars.setdefault(k, {}).setdefault(t[1], []).append((rel, t))
                    continue
                key = (t[0], t[1])
                prev = local.get(key, t)
                # None is the ambiguous verdict, and it is final: a third declaration
                # agreeing with the first does not make the value one type again.
                same = key not in local or (prev is not None and prev[2:] == t[2:])
                local[key] = t if same else None
            self.local[rel] = local
            if not k[1] and rel not in self.modless:
                self.by_path[k[0]] = k
                self.clause.setdefault(k[0], r["pkg"])
        for rel, r in self.raws.items():
            names = {}
            for alias, path in r["binds"]:
                if alias in ("_", "."):
                    continue
                names[alias or self.clause.get(path) or _default_name(path)] = path
            self.imports[rel] = names

    # --- lookup -----------------------------------------------------------------------

    def _chain(self, k: Key) -> list[Key]:
        base = (k[0], False)
        return [k, base] if k[1] and base in self.defs else [k]

    def _find(self, k: Key, qual: str) -> tuple[Key, str] | None:
        """(package, file) defining ``qual`` for code in package ``k``; None if absent
        or defined by more than one file (a build-tag split - which one is live?)."""
        for c in self._chain(k):
            rels = self.defs.get(c, {}).get(qual)
            if rels:
                return (c, rels[0]) if len(rels) == 1 else None
        return None

    def _package(self, rel: str, path: str) -> Key | str | None:
        """The in-repo package ``path`` names for ``rel``; _EXT outside every module."""
        found = None if rel in self.modless else self.by_path.get(path)
        if found is not None:
            return found
        return None if self.inside(path) else _EXT

    # --- types ------------------------------------------------------------------------

    def _named(self, rel: str, k: Key, qualifier: str, name: str) -> Ty:
        """The type ``qualifier.name`` written in ``rel`` (package ``k``)."""
        if qualifier == "":
            found = self._find(k, name)
            return (found[0], name) if found else (_EXT if name in _BUILTINS else None)
        path = self.imports[rel].get(qualifier)
        pk = self._package(rel, path) if path else None
        return (pk, name) if isinstance(pk, tuple) else pk

    def _made_by(self, rel: str, k: Key, qualifier: str, name: str) -> Ty:
        """The first declared result of calling ``qualifier.name()`` from ``rel``."""
        dk, drel = k, rel
        if qualifier:
            path = self.imports[rel].get(qualifier)
            pk = self._package(rel, path) if path else None
            if not isinstance(pk, tuple):
                return pk
            dk = pk
        found = self._find(dk, name)
        if found is None:
            return None
        dk, drel = found
        result = self.res[drel].get(name)
        return self._named(drel, dk, *result) if result else None

    def _of(self, rel: str, k: Key, rec: list) -> Ty:
        if rec[2] == "t":
            return self._named(rel, k, rec[3], rec[4])
        if rec[2] == "c":
            return self._made_by(rel, k, rec[3], rec[4])
        return None

    def _package_var(self, k: Key, var: str) -> tuple[bool, Ty]:
        for c in self._chain(k):
            found = self.pvars.get(c, {}).get(var)
            if found:
                if any(f[1][2:] != found[0][1][2:] for f in found):
                    return True, None
                return True, self._of(found[0][0], c, found[0][1])
        return False, None

    # --- calls ------------------------------------------------------------------------

    def _member(self, ty: Ty, attr: str) -> tuple[str, str] | str | None:
        if not isinstance(ty, tuple):
            return ty
        found = self._find(ty[0], f"{ty[1]}.{attr}")
        return (found[1], f"{ty[1]}.{attr}") if found else None

    def _call(self, rel: str, rec: list) -> tuple[str, str] | str | None:
        caller, form, x, y = rec[0], rec[1], rec[2], rec[3]
        k, local = self.key[rel], self.local[rel]
        shadow = local.get((caller, x), 0) if caller != MODULE else 0
        if form == "n":
            if shadow != 0:
                return None  # a parameter or local named like a function: a value
            found = self._find(k, x)
            if found:
                return found[1], x
            return _EXT if x in _BUILTINS else None
        if shadow != 0:
            return self._member(None if shadow is None else self._of(rel, k, shadow), y)
        path = self.imports[rel].get(x)
        if path is not None:
            pk = self._package(rel, path)
            if not isinstance(pk, tuple):
                return pk
            found = self._find(pk, y)
            return (found[1], y) if found else None
        known, ty = self._package_var(k, x)
        return self._member(ty, y) if known else None

    def _ext_name(self, rel: str, rec: list) -> str | None:
        """``<import path>.<name>`` for a call into a package outside the repo."""
        if rec[1] != "a" or (rec[0] != MODULE and (rec[0], rec[2]) in self.local[rel]):
            return None
        path = self.imports[rel].get(rec[2])
        return f"{path}.{rec[3]}" if path else None

    def link(self) -> Symbols:
        out = self.out
        col = surface_link.Collector("go", out.defs)
        value = value_resolver(self.local, self.key, self.pvars, self._chain)
        for rel, r in self.raws.items():
            col.file(rel, r)
            for rec in r["calls"]:
                target = self._call(rel, rec)
                node = target if isinstance(target, tuple) else None
                col.call(rel, rec, node, self._ext_name(rel, rec) if target == _EXT else None)
                if target == _EXT:
                    out.external += 1
                elif target is None:
                    out.unresolved += 1
                else:
                    out.resolved += 1
                    key = (rel, rec[0], target[0], target[1])
                    if (key[0], key[1]) != (key[2], key[3]):
                        out.edges[key] = out.edges.get(key, 0) + 1

            def resolve(c: list, rel=rel):
                t = self._call(rel, c)
                return t if isinstance(t, tuple) else None

            col.close(rel, resolve, value)
        col.finish()
        out.sinks, out.entries = col.sinks, col.entries
        return out


def link(
    raws: dict[str, dict], placed: dict[str, str | None], inside: Callable[[str], bool]
) -> Symbols:
    """Resolve every file's ``go_symbols_walk`` records (path -> raw).

    ``placed`` maps a file to its package's import path (None: no go.mod owns it);
    ``inside`` says whether an import path lies in an in-repo module."""
    return _Linker(raws, placed, inside).link()
