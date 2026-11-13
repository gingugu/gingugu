"""X7d: Python entries that name a class - class-based views, DRF routers and
flask-restx / flask-restful resources.

- ``handler(arg, cls)``: how a handler argument names its target as the record's
  ``(form, x, y)``: ``name`` (n), ``mod.name`` (a), ``self.name`` (s), and
  ``X.as_view(...)`` / ``mod.X.as_view(...)`` the class ``X`` / ``mod.X``. None:
  not handler-shaped (``make_view()`` is no handler).
- ``Views.decorated``: a class under an HTTP route decorator is one ``"d"`` entry
  per view method (``surface_catalog.PY_VIEW_METHODS``) the class body defines,
  per literal path argument (at most ``surface_route_py.MAX_PATHS``). None: nothing.
- ``Views.resource``: ``api.add_resource(Cls, p1, p2...)`` expands the same way
  when ``Cls`` is defined anywhere in the file, before or after the call, else is
  one entry per path naming ``Cls``. Because the class may come later, the record
  is settled by ``Views.settle`` once the whole file is read.
- ``register``: ``R.register("prefix", ViewSet)`` is the list and the detail
  route, only when ``R`` is a plain name the file last assigns from a constructor
  ending in ``Router`` (``trailing_slash=False`` there drops the trailing slashes).
"""

from __future__ import annotations

import ast

from . import surface_catalog as cat
from . import surface_route as sr
from . import surface_route_py as route

Handler = tuple[str, str, str]


class Later:
    """An ``add_resource`` entry waiting to learn whether its class is in the file."""

    def __init__(self, base: list, name: str, routes: list[str]) -> None:
        self.base, self.name, self.routes = base, name, routes


def handler(arg: ast.expr, cls: str | None) -> Handler | None:
    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute):
        if arg.func.attr != "as_view":
            return None
        arg = arg.func.value
        cls = None  # `self.X.as_view()` is not a class this walk can name
    if isinstance(arg, ast.Name):
        return "n", arg.id, ""
    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
        base = arg.value.id
        if cls is not None and base in ("self", "cls"):
            return "s", cls, arg.attr
        return "a", base, arg.attr
    return None


def _entries(base: list, qual: str, methods: list[str], routes: list[str]) -> list[list]:
    scope, label, line, recv = base
    return [
        [scope, label, "d", f"{qual}.{m}", "", line, 0, 0, m.upper(), r, recv]
        for m in methods
        for r in routes
    ]


class Views:
    """The classes a file defines and the entries that wait on them."""

    def __init__(self) -> None:
        self._defs: dict[str, tuple[str, list[str]]] = {}

    def define(self, node: ast.AST, qual: str) -> None:
        if not isinstance(node, ast.ClassDef):
            return
        prior = self._defs.get(node.name)
        if prior is None or prior[0].count(".") > qual.count("."):
            self._defs[node.name] = (qual, _methods_of(node))

    def decorated(
        self,
        node: ast.AST,
        scope: str,
        qual: str,
        deco: ast.expr,
        label: str,
        lookup: route.Lookup,
        recv: str,
    ) -> list[list]:
        """The entries a route decorator on class ``qual`` makes."""
        if not isinstance(deco, ast.Call):
            return []
        routes = route.paths(deco, label, lookup)
        return _entries([scope, label, deco.lineno, recv], qual, _methods_of(node), routes)

    def resource(
        self, node: ast.Call, label: str, scope: str, lookup: route.Lookup, recv: str
    ) -> list:
        """The (possibly pending) entries of ``api.add_resource(Cls, paths...)``."""
        if not node.args:
            return []
        target = handler(node.args[0], None)
        if target is None or target[0] == "s":
            return []
        routes = route.paths(node, label, lookup, 1)
        if not routes:
            return []
        form, x, y = target
        base = [scope, label, form, x, y, node.lineno, 0, 0, "", "", recv]
        if form == "a":
            return [[*base[:9], r, recv] for r in routes]
        return [Later(base, x, routes)]

    def settle(self, items: list) -> list[list]:
        """``items`` with every ``Later`` replaced by its records."""
        out: list[list] = []
        for it in items:
            if not isinstance(it, Later):
                out.append(it)
                continue
            found = self._defs.get(it.name)
            b = it.base
            if found is None:
                out.extend([*b[:9], r, b[10]] for r in it.routes)
            else:
                out.extend(_entries([b[0], b[1], b[5], b[10]], found[0], found[1], it.routes))
        return out


def _methods_of(node: ast.AST) -> list[str]:
    body = getattr(node, "body", [])
    have = [
        n.name
        for n in body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in cat.PY_VIEW_METHODS
    ]
    return list(dict.fromkeys(have))


def register(node: ast.Call, label: str, scope: str, lookup: route.Lookup, recv: str) -> list[list]:
    """The two DRF entries of ``R.register("prefix", ViewSet)``; [] when it is not one."""
    if label.count(".") != 1 or len(node.args) < 2:
        return []
    made = lookup(label.partition(".")[0])
    if not isinstance(made, ast.Call) or not route._ctor(made).endswith("Router"):
        return []
    raw = route._literal(node.args[0])
    target = handler(node.args[1], None)
    if raw is None or target is None or target[0] == "s":
        return []
    slash_kw = route._kw(made, "trailing_slash")
    slash = "" if isinstance(slash_kw, ast.Constant) and slash_kw.value is False else "/"
    body = raw.strip("/")
    ok = not raw or bool(sr.clean(raw))
    listing = sr.clean("/" + body + slash if body else "/") if ok else ""
    detail = sr.clean((f"/{body}" if body else "") + "/{pk}" + slash) if ok else ""
    form, x, y = target
    return [[scope, label, form, x, y, node.lineno, 0, 0, "", r, recv] for r in (listing, detail)]
