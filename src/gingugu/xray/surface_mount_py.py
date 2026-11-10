"""X7d: the Python walk's router receivers and mounts (``surface_records``' ``recv``,
``mounts`` and, for the linker, ``vars``).

``surface_walk_py.Surface`` hands each def/class, decorator, registrar and call here.

- ``recv(scope, label)``: the root name of the receiver in ``label`` (``router`` for
  ``router.get``); ``#<i>`` when that name is the i-th positional parameter of
  ``scope`` (``self`` counts, parameters in the order written); ``""`` for a label
  with no receiver (``shared_task``, Django's ``path``).
- mounts: ``P.include_router(child, prefix=...)`` (FastAPI), ``P.register_blueprint(
  child, url_prefix=...)`` (Flask) and Django ``path("api/", include("app.urls"))``.
  A literal prefix is joined behind the one the same file gives ``P``; a prefix that
  is not a literal is ``surface_route.PREFIX``; none is the parent's alone.
- ``vars``: ``[scope, name, home]`` for every receiver or child name the file uses
  that is assigned in ``scope`` or a scope enclosing it (class bodies excepted, as
  Python scopes them); ``home`` is the innermost such scope. The linker turns it
  into a router value without re-reading the file.
"""

from __future__ import annotations

import ast

from . import surface_route as sr
from . import surface_route_py as route

MODULE = "<module>"
_MOUNTS = {"include_router": "prefix", "register_blueprint": "url_prefix"}
_CHILD_KW = ("router", "blueprint")
_MAX_PARAM = 99  # surface_records.is_recv allows `#` and two digits


def _root(expr: ast.expr) -> str | None:
    """The Name at the bottom of an Attribute chain, else None."""
    while isinstance(expr, ast.Attribute):
        expr = expr.value
    return expr.id if isinstance(expr, ast.Name) else None


def _name_of(fn: ast.expr) -> str:
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")


def _dotted_module(value: str) -> bool:
    return bool(value) and all(p.isidentifier() for p in value.split("."))


class Mounts:
    def __init__(self, assigns: dict[str, dict[str, list]]) -> None:
        self.mounts: list[list] = []
        self._assigns = assigns
        self._params: dict[str, list[str]] = {}
        self._classes: set[str] = set()
        self._vars: dict[tuple[str, str], str] = {}

    @property
    def vars(self) -> list[list]:
        return [[s, n, h] for (s, n), h in self._vars.items()]

    def definition(self, node: ast.AST, qual: str) -> None:
        """A def or class has been entered; ``qual`` is its qualified name."""
        if isinstance(node, ast.ClassDef):
            self._classes.add(qual)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            self._params[qual] = [p.arg for p in (*a.posonlyargs, *a.args)]

    def recv(self, scope: str, label: str) -> str:
        """The receiver root of a decorator or registrar ``label``; "" when none."""
        root, dot, _ = label.partition(".")
        return self._root(scope, root) if dot else ""

    def _root(self, scope: str, name: str) -> str:
        params = self._params.get(scope, [])
        if name in params and params.index(name) <= _MAX_PARAM:
            return f"#{params.index(name)}"
        home = self._home(scope, name)
        if home is not None:
            self._vars[(scope, name)] = home
        return name

    def _home(self, scope: str, name: str) -> str | None:
        """The innermost scope from ``scope`` outwards that assigns ``name``."""
        chain = [scope]
        if scope != MODULE:
            parts = scope.split(".")
            for i in range(len(parts) - 1, 0, -1):
                outer = ".".join(parts[:i])
                if outer not in self._classes:
                    chain.append(outer)
            chain.append(MODULE)
        return next((s for s in chain if name in self._assigns.get(s, {})), None)

    def call(self, node: ast.Call, scope: str, lookup: route.Lookup) -> None:
        """Record the mount ``node`` makes, if it is one."""
        fn = node.func
        attr = _name_of(fn)
        if attr in _MOUNTS and isinstance(fn, ast.Attribute):
            self._router_mount(node, fn, scope, lookup, _MOUNTS[attr])
        elif attr in ("path", "re_path"):
            self._django(node, scope)

    def _router_mount(
        self, node: ast.Call, fn: ast.Attribute, scope: str, lookup: route.Lookup, kw: str
    ) -> None:
        child = node.args[0] if node.args else route._kw(node, *_CHILD_KW)
        if isinstance(child, ast.Name):
            form, x, y = "n", child.id, ""
        elif isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            form, x, y = "a", child.value.id, child.attr
        else:
            return
        root = _root(fn.value)
        parent = self._root(scope, root) if root else ""
        base = route._prefix(f"{root}.x", lookup) if isinstance(fn.value, ast.Name) else ""
        arg = route._kw(node, kw)
        lit = route._literal(arg)
        if arg is None:
            own = ""
        else:
            own = sr.PREFIX if lit is None else sr.clean(lit) or (sr.PREFIX if lit else "")
        if form == "n":
            self._root(scope, x)  # the linker needs the child's home scope too
        self.mounts.append([scope, node.lineno, parent, sr.join(base, own), form, x, y, 0])

    def _django(self, node: ast.Call, scope: str) -> None:
        if len(node.args) < 2 or not isinstance(node.args[1], ast.Call):
            return
        inc = node.args[1]
        mod = route._literal(inc.args[0]) if inc.args else None
        lit = route._literal(node.args[0])
        if _name_of(inc.func) != "include" or mod is None or lit is None:
            return
        if not _dotted_module(mod):
            return
        prefix = sr.clean(lit) or (sr.PREFIX if lit else "")
        self.mounts.append([scope, node.lineno, "", prefix, "m", mod, "", 0])
