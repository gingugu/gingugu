"""The resolution half of the TS/JS symbol graph: walk records in, call edges out.

The same promise as ``symbols`` for Python: a call resolves only through what
static reading can prove - a definition in an enclosing scope (class bodies
excepted), an import or ``require`` binding followed through barrels to a
top-level definition or a class's method, a value whose class is named by
``new``, an annotation or a function's declared return type, and ``this``
inside a class. Everything else is counted external (a global, a package, a
primitive, anything an external call returned) or unresolved, never guessed:
a lower bound.
"""

from __future__ import annotations

from .. import surface_link
from ..symbols import MODULE, Symbols
from .ts_exports import EXT, Modules
from .ts_resolve import Resolver

MAX_DEPTH = 6  # how many calls deep a value's type is chased (`a = b.f()`, `b = c.g()`, ...)
JS_GLOBALS = frozenset(
    """console Object Array JSON Math Promise String Number Boolean Date Error Map Set Symbol
    Reflect parseInt parseFloat setTimeout clearTimeout setInterval clearInterval require process
    Buffer RegExp isNaN isFinite fetch structuredClone queueMicrotask encodeURIComponent
    decodeURIComponent encodeURI decodeURI globalThis describe it test expect beforeEach afterEach
    beforeAll afterAll vi jest window document navigator location URL URLSearchParams TextEncoder
    TextDecoder WeakMap WeakSet WeakRef BigInt Intl Proxy AbortController Event EventTarget
    FormData Headers Request Response Blob File ArrayBuffer Uint8Array Float64Array Int32Array
    DataView Atomics SharedArrayBuffer TypeError RangeError SyntaxError ReferenceError
    requestAnimationFrame cancelAnimationFrame setImmediate clearImmediate atob btoa
    alert""".split()
)
_AMBIGUOUS = object()  # a name declared twice, differently, in one scope

Target = tuple | str | None  # ("mod", path) | ("sym", path, qual) | EXT | None


class _Linker:
    def __init__(self, raws: dict[str, dict], mods: Modules) -> None:
        self.raws, self.mods = raws, mods
        self.out = Symbols(
            defs={rel: {d[0]: (d[1], d[2], d[3]) for d in raw["defs"]} for rel, raw in raws.items()}
        )
        self._tables: dict[str, tuple[dict, dict, dict]] = {}
        self._bound_cache: dict[tuple[str, str], Target] = {}

    def _defs(self, path: str) -> dict:
        return self.out.defs.get(path, {})

    def _table(self, rel: str) -> tuple[dict, dict, dict]:
        """``(binds, types, rets)`` of one file; the last binding of a name wins, a
        variable declared twice with different types is ambiguous."""
        if rel not in self._tables:
            raw = self.raws[rel]
            types: dict[tuple[str, str], object] = {}
            for t in raw["types"]:
                key, ref = (t[0], t[1]), tuple(t[2:])
                types[key] = ref if types.get(key, ref) == ref else _AMBIGUOUS
            self._tables[rel] = (
                {b[1]: b for b in raw["binds"]},
                types,
                {r[0]: tuple(r[1:]) for r in raw["rets"]},
            )
        return self._tables[rel]

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

    def _bound(self, rel: str, name: str) -> Target:
        key = (rel, name)
        if key not in self._bound_cache:
            self._bound_cache[key] = self._bind(rel, self._table(rel)[0][name])
        return self._bound_cache[key]

    def _bind(self, rel: str, bind: list) -> Target:
        path = self.mods.target(rel, bind[2])
        if path is None or path == EXT:
            return path
        if bind[0] == "i":
            return ("mod", path)
        hit = self.mods.export_of(path, bind[3])
        return ("sym", *hit) if hit else None

    def _name(self, rel: str, caller: str, name: str) -> Target:
        defs = self._defs(rel)
        for scope in self._scopes(rel, caller):
            qual = f"{scope}.{name}" if scope else name
            if qual in defs:
                return ("sym", rel, qual)
        if name in self._table(rel)[0]:
            return self._bound(rel, name)
        return EXT if name in JS_GLOBALS else None

    def _member(self, target: Target, attr: str) -> Target:
        """``attr`` of a module (an export) or of a class (a method)."""
        if not isinstance(target, tuple):
            return None
        if target[0] == "mod":
            hit = self.mods.export_of(target[1], attr)
            return ("sym", *hit) if hit else None
        path, qual = target[1], target[2]
        if self._defs(path)[qual][0] != "class":
            return None
        member = f"{qual}.{attr}"
        return ("sym", path, member) if member in self._defs(path) else None

    def _typed(self, rel: str, caller: str, var: str):
        """``(found, ref, scope)`` for ``var`` in ``caller`` or a scope enclosing it."""
        types = self._table(rel)[1]
        for scope in self._scopes(rel, caller):
            ref = types.get((scope or MODULE, var))
            if ref is not None:
                return True, (None if ref is _AMBIGUOUS else ref), scope or MODULE
        return False, None, MODULE

    def _class(self, rel: str, scope: str, ref: tuple, depth: int) -> Target:
        """What a type reference stands for: EXT for a primitive or an external class,
        ``("sym", path, qual)`` of a class, else None."""
        form, x, y = ref
        if form == "p":
            return EXT
        if form in ("n", "a"):
            target = self._name(rel, scope, x)
            if form == "a" and target != EXT:
                target = self._member(target, y)
        elif form in ("c", "d") and depth < MAX_DEPTH:
            callee = self._callee(rel, scope, "n" if form == "c" else "a", x, y, depth + 1)
            if not isinstance(callee, tuple):
                return callee  # EXT (an external call returns an external value) or None
            declared = self._table(callee[1])[2].get(callee[2])
            return self._class(callee[1], callee[2], declared, depth + 1) if declared else None
        else:
            return None
        if target == EXT:
            return EXT
        if isinstance(target, tuple) and target[0] == "sym":
            return target if self._defs(target[1])[target[2]][0] == "class" else None
        return None

    def _callee(self, rel: str, caller: str, form: str, x: str, y: str, depth: int = 0) -> Target:
        """The target of ``x()`` (form "n") or ``x.y()`` (form "a"): EXT, a symbol, or None."""
        found, ref, scope = self._typed(rel, caller, x)
        if form == "n":
            if found:  # a variable shadows any definition or global of the name
                return None
            target = self._name(rel, caller, x)
            return target if target == EXT or (target and target[0] == "sym") else None
        if found:
            cls = self._class(rel, scope, ref, depth) if ref else None
            return cls if cls == EXT else self._member(cls, y)
        target = self._name(rel, caller, x)
        return target if target == EXT else self._member(target, y)

    def _call(self, rel: str, rec: list) -> Target:
        caller, form, x, y = rec[0], rec[1], rec[2], rec[3]
        if form == "s":
            qual = f"{x}.{y}"
            return ("sym", rel, qual) if qual in self._defs(rel) else None
        return self._callee(rel, caller, form, x, y)

    def _ext_name(self, rel: str, rec: list) -> str | None:
        """The dotted name a call leaving the repo targets (``child_process.exec``,
        ``fs.writeFile``), or the global it names (``eval``, ``fetch``)."""
        caller, form, x, y = rec[0], rec[1], rec[2], rec[3]
        if form == "s" or self._typed(rel, caller, x)[0]:
            return None
        defs = self._defs(rel)
        if any((f"{s}.{x}" if s else x) in defs for s in self._scopes(rel, caller)):
            return None
        bind = self._table(rel)[0].get(x)
        if bind is None:
            base = x
        else:
            spec = bind[2].removeprefix("node:")
            base = spec if bind[0] == "i" or bind[3] == "default" else f"{spec}.{bind[3]}"
        return base if form == "n" else f"{base}.{y}"

    def link(self) -> Symbols:
        out = self.out
        col = surface_link.Collector("typescript", out.defs)
        for rel, raw in self.raws.items():
            col.file(rel, raw)
            for rec in raw["calls"]:
                target = self._call(rel, rec)
                node = target[1:] if isinstance(target, tuple) else None
                col.call(rel, rec, node, None if node else self._ext_name(rel, rec))
                if target == EXT:
                    out.external += 1
                elif target is None:
                    out.unresolved += 1
                else:
                    out.resolved += 1
                    key = (rel, rec[0], target[1], target[2])
                    if (key[0], key[1]) != (key[2], key[3]):
                        out.edges[key] = out.edges.get(key, 0) + 1

            def resolve(r: list, rel=rel):
                t = self._call(rel, r)
                return t[1:] if isinstance(t, tuple) else None

            col.close(rel, resolve)
        out.sinks, out.entries = col.sinks, col.entries
        return out


def link(raws: dict[str, dict], resolver: Resolver) -> Symbols:
    """Resolve every file's ``ts_symbols_walk`` records (path -> raw) through ``resolver``."""
    return _Linker(raws, Modules(raws, resolver)).link()
