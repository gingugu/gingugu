"""X7d for TS/JS: the walk side of cross-file route mounts (``surface_records``).

``MountScan`` rides the symbol walk beside ``ts_surface_walk.Surface`` and keeps:

- ``decls``: ``[scope, name]`` for each ``const``/``let``/``var`` an identifier is
  declared by (a ``require`` binding is a bind, not a decl) - the linker's "is this
  name a value declared here" answer;
- router names: a name this file declares from ``express()``, ``express.Router()`` or
  ``Router()`` is router-shaped like ``app`` or ``usersRouter`` (``cat.router_shaped``);
- parameters by function scope, so a receiver that is the i-th parameter reads ``#<i>``;
- mount sites: ``P.use("/lit", child)``, a router handed to a function
  (``register(api)``: form ``pn``/``pa``, the passed router is the mount's parent),
  ``X.setGlobalPrefix("lit")`` (form ``g``).

Whether a receiver is router-shaped can depend on a declaration further down the
file, so candidates are kept raw and settled in ``finish``. Loops, no recursion.
"""

from __future__ import annotations

from .. import surface_catalog as cat
from .. import surface_route as sr
from ..symbols_walk import is_qual
from . import ts_surface_route as tr
from .ts_symbols_types import text

MAX_MOUNTS = 2000
MAX_DECLS = 20000
MAX_ARG = 63
_STR = ("string", "template_string")


def ident(name: object) -> bool:
    return isinstance(name, str) and len(name) <= 200 and is_qual(name) and "." not in name


def is_require(value) -> str | None:
    """The literal specifier of ``require("x")``; None for anything else."""
    if value is None or value.type != "call_expression":
        return None
    fn, args = value.child_by_field_name("function"), value.child_by_field_name("arguments")
    if fn is None or fn.type != "identifier" or text(fn) != "require" or args is None:
        return None
    first = next(iter(args.named_children), None)
    return (tr.literal(first) or None) if first is not None and first.type in _STR else None


def _makes_router(value) -> bool:
    if value is None or value.type != "call_expression":
        return False
    fn = value.child_by_field_name("function")
    if fn is None:
        return False
    if fn.type == "identifier":
        return text(fn) in ("express", "Router")
    obj, prop = fn.child_by_field_name("object"), fn.child_by_field_name("property")
    return bool(
        fn.type == "member_expression"
        and obj is not None
        and prop is not None
        and obj.type == "identifier"
        and text(obj) == "express"
        and text(prop) == "Router"
    )


def module_export(node) -> str | None:
    """The identifier ``module.exports = X`` hands out, else None."""
    left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
    if left is None or right is None or right.type != "identifier":
        return None
    if text(left) != "module.exports" or not ident(text(right)):
        return None
    return text(right)


def _child(node) -> tuple[str, str, str] | None:
    """(form, x, y) of a mounted expression: a name, ``ns.name``, ``require("m")``, or a
    call of a function / ``ns.function`` that builds the router."""
    if node.type == "identifier":
        return ("n", text(node), "") if ident(text(node)) else None
    if node.type == "member_expression":
        obj, prop = node.child_by_field_name("object"), node.child_by_field_name("property")
        if obj is not None and prop is not None and obj.type == "identifier":
            if prop.type == "property_identifier" and ident(text(obj)) and ident(text(prop)):
                return "a", text(obj), text(prop)
        return None
    if node.type != "call_expression":
        return None
    spec = is_require(node)
    if spec is not None:
        return ("r", spec, "") if sr.clean(spec) == spec else None
    fn = node.child_by_field_name("function")
    inner = _child(fn) if fn is not None and fn.type != "call_expression" else None
    return (f"c{inner[0]}", inner[1], inner[2]) if inner and inner[0] in ("n", "a") else None


class MountScan:
    def __init__(self) -> None:
        self.decls: list[list] = []
        self.routers: set[str] = set()
        self._seen: set[tuple[str, str]] = set()
        self._params: dict[str, list[str | None]] = {}
        self._sites: list[tuple] = []
        self._pending: list[tuple[list, str]] = []

    def params(self, fn, scope: str) -> None:
        """The positional parameter names of the function that opens ``scope``."""
        single, group = fn.child_by_field_name("parameter"), fn.child_by_field_name("parameters")
        found = ([single] if single is not None else []) + (
            [c for c in group.named_children if c.type != "comment"] if group is not None else []
        )
        names: list[str | None] = []
        for p in found[: MAX_ARG + 1]:
            if p.type == "assignment_pattern":
                p = p.child_by_field_name("left")
            if p is not None and p.type in ("required_parameter", "optional_parameter"):
                p = p.child_by_field_name("pattern")
            ok = p is not None and p.type == "identifier" and ident(text(p))
            names.append(text(p) if ok else None)
        self._params[scope] = names

    def declarator(self, node, scope: str) -> None:
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if name is None or name.type != "identifier" or is_require(value) is not None:
            return
        n = text(name)
        if not ident(n):
            return
        if (scope, n) not in self._seen and len(self._seen) < MAX_DECLS:
            self._seen.add((scope, n))
            self.decls.append([scope, n])
        if _makes_router(value):
            self.routers.add(n)

    def shaped(self, name: str | None) -> bool:
        return cat.router_shaped(name) or name in self.routers

    def recv(self, parts: list[str], scope: str) -> str:
        """The receiver root of a chain: the name (``#<i>`` when it is parameter i of
        ``scope``); for ``this.app`` / ``a.b.app`` the last name; "" when none fits."""
        if not parts:
            return ""
        name = parts[0] if len(parts) == 1 else parts[-1]
        if name == "this" or not ident(name):
            return ""
        if len(parts) == 1 and name in self._params.get(scope, ()):
            return f"#{self._params[scope].index(name)}"
        return name

    def entry(self, label: str, scope: str) -> str:
        """The ``recv`` of an HTTP entry registered through ``label``; "" otherwise."""
        if "." not in label or cat.entry_kind("typescript", label) != "http":
            return ""
        return self.recv(label.split(".")[:-1], scope)

    @staticmethod
    def maybe(label: str | None) -> bool:
        """Whether ``x.verb`` could be a route call once ``x`` proves a declared router."""
        parts = label.split(".") if label else []
        if len(parts) != 2 or parts[0] == "this" or not ident(parts[0]):
            return False
        return cat.entry_kind("typescript", f"router.{parts[1]}") == "http"

    def pending(self, recs: list[list], name: str) -> None:
        self._pending.extend((r, name) for r in recs)

    def site(self, label: str | None, args: list, scope: str, line: int) -> None:
        """One call that might be a mount; settled in ``finish``."""
        parts = label.split(".") if label else []
        args = [a for a in args if a.type != "comment"]
        if not parts or len(self._sites) >= MAX_MOUNTS:
            return
        last = parts[-1]
        if last == "setGlobalPrefix" and args and args[0].type in _STR:
            if tr.literal(args[0]):
                self._sites.append(("g", scope, line, tr.literal(args[0])))
        elif last == "use" and len(parts) > 1:
            child = _child(args[-1]) if len(args) >= 2 and args[0].type in _STR else None
            if child is not None:
                prefix = tr.literal(args[0]) or sr.PREFIX
                self._sites.append(("use", scope, line, parts[:-1], prefix, child))
        elif len(parts) <= 2 and parts[0] != "this" and last != "require":
            for i, a in enumerate(args[: MAX_ARG + 1]):
                if a.type == "identifier" and ident(text(a)):
                    self._sites.append(("pass", scope, line, parts, text(a), i))

    def finish(self, entries: list[list]) -> list[list]:
        """Settle what depended on the whole file: declared-router entries, then the
        mount records. Mutates ``entries`` (drops non-routers, relabels the rest)."""
        drop = set()
        for rec, name in self._pending:
            if name in self.routers:
                rec[1] = f"router.{rec[1].rsplit('.', 1)[-1]}"
                rec[10] = self.recv([name], rec[0])
            else:
                drop.add(id(rec))
        entries[:] = [e for e in entries if id(e) not in drop]
        out = (self._mount(site) for site in self._sites)
        return [rec for rec in out if rec is not None]

    def _mount(self, site: tuple) -> list | None:
        kind, scope, line = site[:3]
        if kind == "g":
            return [scope, line, "", site[3], "g", "", "", 0]
        if kind == "use":
            parts, prefix, (form, x, y) = site[3:]
            if not self.shaped(parts[-1]):
                return None
            return [scope, line, self.recv(parts, scope), prefix, form, x, y, 0]
        parts, arg, index = site[3:]
        last = parts[-1]
        if not self.shaped(arg) or last == "use" or (len(parts) == 2 and self.shaped(parts[0])):
            return None
        if cat.entry_kind("typescript", f"router.{last}") == "http":
            return None
        x, y = (parts[0], "") if len(parts) == 1 else (parts[0], parts[1])
        if not (ident(x) and (not y or ident(y))):
            return None
        return [scope, line, self.recv([arg], scope), "", "pn" if not y else "pa", x, y, index]
