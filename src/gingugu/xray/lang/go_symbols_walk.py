"""The AST half of the Go symbol graph: one parsed file in, JSON-safe records out.

Like ``symbols_walk`` this depends on the file's bytes alone - never on where
the file sits or what else is in the repo - so the result is cached by content
and ``go_symbols.link`` resolves it against the repo later. Go resolves across
a whole package, so nothing here looks past one file.

Records (lists, so a JSON round trip is the identity; lines are 1-based):

- ``pkg``: the package clause name.
- binds: ``[alias_or_empty, import path]``.
- def: ``[qual, kind, line, end]``; a function is ``Name``, a method
  ``Type.Name`` (receiver base type, through ``*``, type parameters and
  parentheses), a type ``Name`` (kind "class"). Interface methods are not defs.
- call: ``[caller, form, x, y, line]``; form "n" is ``x()``, "a" is ``x.y()``
  on a bare name. Calls on anything else are only counted in ``other``.
- type: ``[scope, var, form, x, y]``; form "t": ``var`` holds a ``x.y`` (or, with
  ``x`` empty, a package-local ``y``); form "c": it holds the first result of
  calling ``x.y()`` (typed at link time, from the callee's declared result);
  form "?": it is declared but its type is not modelled - it shadows, and stays
  unresolved. ``scope`` is the enclosing function's qual or ``<module>``.
- results: ``[qual, x, y]``, the declared first result type of a function.
- entries, sql: the X7d records (``go_surface_walk``; shape in ``surface_records``).
"""

from __future__ import annotations

from ..symbols_walk import MODULE, is_qual
from .go_surface_walk import surface
from .go_symbols_valid import ident, is_bind, is_call, is_def, is_result, is_type, valid

__all__ = ["base_type", "valid", "walk"]
_FUNCS = ("function_declaration", "method_declaration")


def _txt(n) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _line(n) -> int:
    return n.start_point[0] + 1


def base_type(n) -> tuple[str, str] | None:
    """``(package qualifier or "", name)`` behind ``*T``, ``T``, ``pkg.T``, ``T[A]``."""
    while n is not None and n.type in ("pointer_type", "generic_type", "parenthesized_type"):
        n = n.child_by_field_name("type") or (n.named_children[0] if n.named_children else None)
    if n is None:
        return None
    if n.type == "type_identifier":
        return "", _txt(n)
    if n.type == "qualified_type":
        return _txt(n.child_by_field_name("package")), _txt(n.child_by_field_name("name"))
    return None


def _callee(call) -> tuple[str, str] | None:
    """``(qualifier, name)`` of a call to ``f()`` or ``pkg.f()``, else None."""
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return "", _txt(fn)
    if fn.type == "selector_expression":
        op = fn.child_by_field_name("operand")
        if op is not None and op.type == "identifier":
            return _txt(op), _txt(fn.child_by_field_name("field"))
    return None


def _value_type(v) -> tuple[str, str, str] | None:
    """``(form, x, y)`` for ``T{}``, ``&T{}`` or a call - the shapes that type a value."""
    if v is None:
        return None
    if v.type == "unary_expression" and v.children and v.children[0].type == "&":
        v = v.child_by_field_name("operand")
        if v is None:
            return None
    if v.type == "composite_literal":
        bt = base_type(v.child_by_field_name("type"))
        return ("t", *bt) if bt else None
    if v.type == "call_expression":
        callee = _callee(v)
        return ("c", *callee) if callee else None
    return None


class _Walk:
    def __init__(self) -> None:
        self.pkg = ""
        self.out: dict[str, list] = {k: [] for k in ("binds", "defs", "calls", "types", "results")}
        self.other = 0
        self.scopes: dict[tuple[int, int], str | None] = {}  # function span -> qual (X7d)
        self.lit = (
            False  # inside a function literal at package level: its locals are not package vars
        )

    def type(self, scope: str | None, var: str, ref: tuple[str, str, str] | None) -> None:
        if scope is None or not ident(var) or (scope == MODULE and self.lit):
            return
        rec = [scope, var, *(ref or ("?", "", ""))]
        if is_type(rec):
            self.out["types"].append(rec)

    def params(self, plist, scope: str | None) -> None:
        for c in plist.named_children if plist is not None else ():
            if c.type not in ("parameter_declaration", "variadic_parameter_declaration"):
                continue
            bt = (
                base_type(c.child_by_field_name("type"))
                if c.type == "parameter_declaration"
                else None
            )
            for nm in c.children_by_field_name("name"):
                self.type(scope, _txt(nm), ("t", *bt) if bt else None)

    def declare(self, scope: str | None, names: list, vals: list) -> None:
        """Type ``names`` from ``vals``: pairwise, or the first from a lone call."""
        for i, nm in enumerate(names):
            paired = len(vals) == len(names) or (len(vals) == 1 and i == 0)
            self.type(
                scope, _txt(nm), _value_type(vals[i if len(vals) > 1 else 0]) if paired else None
            )

    def func(self, node, stack: list) -> None:
        name = _txt(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        qual, recv = name, None
        if node.type == "method_declaration":
            rp = node.child_by_field_name("receiver")
            kids = rp.named_children if rp is not None else []
            pd = next((c for c in kids if c.type == "parameter_declaration"), None)
            bt = base_type(pd.child_by_field_name("type")) if pd is not None else None
            if bt is None or bt[0] != "":
                qual = ""  # a receiver that names no local type: no def, its calls are "other"
            else:
                qual, recv = f"{bt[1]}.{name}", pd.child_by_field_name("name")
        self.scopes[(node.start_byte, node.end_byte)] = qual if is_qual(qual) else None
        if not is_qual(qual):
            if body is not None:
                stack.append((body, None, False))
            return
        kind = "method" if node.type == "method_declaration" else "function"
        self.out["defs"].append([qual, kind, _line(node), node.end_point[0] + 1])
        if recv is not None:
            self.type(qual, _txt(recv), ("t", "", qual.split(".")[0]))
        self.params(node.child_by_field_name("parameters"), qual)
        res = node.child_by_field_name("result")
        if res is not None and res.type == "parameter_list":
            self.params(res, qual)
            first = next((c for c in res.named_children if c.type == "parameter_declaration"), None)
            res = first.child_by_field_name("type") if first is not None else None
        bt = base_type(res)
        if bt and is_result([qual, *bt]):
            self.out["results"].append([qual, *bt])
        if body is not None:
            stack.append((body, qual, False))

    def call(self, node, scope: str | None) -> None:
        fn = node.child_by_field_name("function")
        rec = None
        if scope is not None and fn is not None:
            if fn.type == "identifier":
                rec = [scope, "n", _txt(fn), "", _line(node)]
            elif fn.type == "selector_expression":
                op = fn.child_by_field_name("operand")
                if op is not None and op.type == "identifier":
                    field = _txt(fn.child_by_field_name("field"))
                    rec = [scope, "a", _txt(op), field, _line(node)]
        if rec is not None and is_call(rec):
            self.out["calls"].append(rec)
        else:
            self.other += 1

    def visit(self, node, scope: str | None) -> None:
        t = node.type
        if t == "package_clause":
            name = _txt(
                next((c for c in node.named_children if c.type == "package_identifier"), None)
            )
            self.pkg = name if ident(name) else ""
        elif t == "import_spec":
            nm = node.child_by_field_name("name")
            rec = [_txt(nm), _txt(node.child_by_field_name("path"))[1:-1]]
            if is_bind(rec):
                self.out["binds"].append(rec)
        elif t in ("type_spec", "type_alias"):
            rec = [
                _txt(node.child_by_field_name("name")),
                "class",
                _line(node),
                node.end_point[0] + 1,
            ]
            if is_def(rec):
                self.out["defs"].append(rec)
        elif t == "call_expression":
            self.call(node, scope)
        elif t == "func_literal":
            self.lit = self.lit or scope == MODULE
            self.params(node.child_by_field_name("parameters"), scope)
            res = node.child_by_field_name("result")
            if res is not None and res.type == "parameter_list":
                self.params(res, scope)
        elif t == "short_var_declaration":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            names = [c for c in left.named_children if c.type == "identifier"] if left else []
            self.declare(scope, names, list(right.named_children) if right else [])
        elif t == "range_clause":
            left = node.child_by_field_name("left")
            for c in left.named_children if left is not None else ():
                if c.type == "identifier":
                    self.type(scope, _txt(c), None)
        elif t == "var_spec":
            names = list(node.children_by_field_name("name"))
            declared = node.child_by_field_name("type")
            value = node.child_by_field_name("value")
            if declared is not None:
                bt = base_type(declared)
                for nm in names:
                    self.type(scope, _txt(nm), ("t", *bt) if bt else None)
            else:
                self.declare(scope, names, list(value.named_children) if value else [])


def walk(root) -> dict:
    """Definitions, imports, call sites and value types of one parsed Go file."""
    w = _Walk()
    stack: list[tuple[object, str | None, bool]] = [(root, MODULE, False)]
    while stack:
        node, scope, lit = stack.pop()
        w.lit = lit
        if node.type in _FUNCS:
            w.func(node, stack)
            continue
        w.visit(node, scope)
        lit = lit or (node.type == "func_literal" and scope == MODULE)
        stack.extend((c, scope, lit) for c in reversed(node.children))
    return {"pkg": w.pkg, **w.out, "other": w.other, **surface(root, w.pkg, w.scopes)}
