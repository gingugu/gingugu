"""X7d for TS/JS: which router a name or expression is, across files.

The linker's ``value(rel, scope, form, x, y)`` for ``surface_link.Collector``:

- ``"n"`` a name: a variable declared in ``scope`` or one enclosing it ->
  ``(rel, that scope, name)``; else an import or ``require`` binding -> the
  exporting file's local name for that export, ``(path, "<module>", local)``.
  A parameter is not a value here (receivers that are parameters are ``#<i>``).
- ``"a"`` ``x.y``: ``x`` a namespace import (or whole-module ``require``) of a repo
  module -> that module's export ``y``.
- ``"r"`` a specifier -> that module's default export.

An export is followed through ``export {x as y}``, ``export {x} from`` and
``export *`` barrels, and a default that is itself an import. A module-level
variable counts as exported under its own name. Loops over a worklist with a
visited set, so cycles and hostile barrel meshes end; remembered per question.
"""

from __future__ import annotations

from ..symbols_walk import MODULE
from .ts_exports import EXT, MAX_HOPS, Modules

Ref = tuple[str, str, str]


class Values:
    def __init__(self, raws: dict[str, dict], mods: Modules) -> None:
        self.raws, self.mods = raws, mods
        self._decls: dict[str, set[tuple[str, str]]] = {}
        self._binds: dict[str, dict[str, list]] = {}
        self._memo: dict[tuple[str, str], Ref | None] = {}

    def _declared(self, rel: str) -> set[tuple[str, str]]:
        if rel not in self._decls:
            self._decls[rel] = {(d[0], d[1]) for d in self.raws.get(rel, {}).get("decls", ())}
        return self._decls[rel]

    def _bound(self, rel: str) -> dict[str, list]:
        if rel not in self._binds:
            self._binds[rel] = {b[1]: b for b in self.raws.get(rel, {}).get("binds", ())}
        return self._binds[rel]

    def __call__(self, rel: str, scope: str, form: str, x: str, y: str) -> Ref | None:
        if rel not in self.raws:
            return None
        if form == "r":
            path = self.mods.target(rel, x)
            return self._export(path, "default") if path and path != EXT else None
        if form == "n":
            return self._name(rel, scope, x)
        if form == "a":
            bind = self._bound(rel).get(x)
            if bind is None or self._shadowed(rel, scope, x) or bind[0] != "i":
                return None
            path = self.mods.target(rel, bind[2])
            return self._export(path, y) if path and path != EXT else None
        return None

    def _shadowed(self, rel: str, scope: str, name: str) -> str | None:
        decls, parts = self._declared(rel), [] if scope == MODULE else scope.split(".")
        for i in range(len(parts), 0, -1):
            if (".".join(parts[:i]), name) in decls:
                return ".".join(parts[:i])
        return MODULE if (MODULE, name) in decls else None

    def _name(self, rel: str, scope: str, name: str) -> Ref | None:
        home = self._shadowed(rel, scope, name)
        if home is not None:
            return (rel, home, name)
        return self._local(rel, name)

    def _local(self, rel: str, name: str) -> Ref | None:
        """The router a module-level name is: its own declaration, or its import."""
        return self._export(rel, "", local=name)

    def _export(self, path: str | None, name: str, local: str | None = None) -> Ref | None:
        if path is None:
            return None
        key = (path, f"@{local}" if local is not None else name)  # "@": not an identifier
        if key not in self._memo:
            self._memo[key] = self._follow(path, name, local)
        return self._memo[key]

    def _follow(self, path: str, name: str, local: str | None) -> Ref | None:
        """Worklist of ``(file, name, is_local)``: a local name is a declaration or an
        import to chase; an export name is a declaration, a clause, or a barrel."""
        seen: set[tuple[str, str, bool]] = set()
        stack = [(path, local if local is not None else name, local is not None, 0)]
        while stack:
            file, want, is_local, hops = stack.pop()
            if (file, want, is_local) in seen or hops > MAX_HOPS or file not in self.raws:
                continue
            seen.add((file, want, is_local))
            if (MODULE, want) in self._declared(file) and (is_local or want != "default"):
                return (file, MODULE, want)
            if is_local:
                bind = self._bound(file).get(want)
                nxt = self.mods.target(file, bind[2]) if bind else None
                if bind and nxt and nxt != EXT:
                    orig = "default" if bind[0] == "i" else bind[3]
                    stack.append((nxt, orig, False, hops + 1))
                continue
            for exported, spec, orig in self.mods.exports.get(file, ()):
                if exported == want and not spec:
                    stack.append((file, orig, True, hops + 1))
                    continue
                if (
                    not spec
                    or exported not in (want, "*")
                    or (exported == "*" and want == "default")
                ):
                    continue
                nxt = self.mods.target(file, spec)
                if nxt and nxt != EXT:
                    stack.append((nxt, orig if exported == want else want, False, hops + 1))
        return None
