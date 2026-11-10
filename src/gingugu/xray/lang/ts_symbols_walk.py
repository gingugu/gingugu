"""The AST half of the TS/JS symbol graph: one parsed tree in, JSON-safe records out.

Like ``symbols_walk`` for Python this depends on the file's bytes alone, so the
result is cached by content and ``ts_symbols.link`` resolves it repo-wide later.
Records (lists, so a JSON round trip is the identity):

- def:    ``[qual, kind, line, end]``; kind is "function", "method" or "class".
- bind:   ``["i", local, spec]`` (namespace import, ``const x = require(spec)``) or
  ``["f", local, spec, name]`` (named, aliased or default import - name "default" -
  and a destructured require).
- call:   ``[caller, form, x, y, line]``; form "n" is ``x()`` (and ``new x()``), "a" is
  ``x.y()`` on a bare name, "s" is ``this.y()`` in a class, ``x`` being the class.
  Any other shape (chains, ``super()``, ``import()``) is only counted in ``other``.
- entries, sql, mounts: the X7d surface records of ``ts_surface_walk`` (see
  ``surface_records``).
- decls:  ``[scope, name]``, a variable declared by name (``ts_surface_mounts``); a
  ``module.exports = x`` is an export record of the default.
- type:   ``[scope, var, form, x, y]``, the forms of ``ts_symbols_types``.
- ret:    ``[qual, form, x, y]``, a function's declared return type (n, a or p).
- export: ``[name, spec, orig]``; a spec means a re-export of ``orig`` from it, and
  name "*" is ``export *``; with no spec it is a local ``export {orig as name}`` or
  the default export (name "default").

Anonymous functions and callbacks open no scope: their calls belong to the
enclosing definition. The walk is a loop over an explicit stack, and a file
that failed to parse still yields whatever tree-sitter recovered.
"""

from __future__ import annotations

from ..symbols_walk import MODULE, is_qual
from . import ts_parse
from .ts_surface_mounts import module_export
from .ts_surface_walk import Surface
from .ts_symbols_types import UNKNOWN, text, type_ref, value_ref
from .ts_symbols_valid import valid

__all__ = ["valid", "walk"]

_DECLS = ("function_declaration", "generator_function_declaration")
_EXPRS = ("function_expression", "function", "generator_function", "arrow_function")
_CLASSES = ("class_declaration", "abstract_class_declaration", "class")
_NAMES = ("identifier", "property_identifier", "type_identifier")

Frame = tuple  # (node, qual, in_class, cls, alias)


def _ident(node) -> str | None:
    """The text of a plain identifier node that may head a qualified name, else None."""
    if node is None or node.type not in _NAMES:
        return None
    name = text(node)
    return name if is_qual(name) and "." not in name else None


def _spec(node) -> str:
    return ts_parse.string_value(node) if node is not None else ""


class _Records:
    def __init__(self) -> None:
        self.defs: list[list] = []
        self.binds: list[list] = []
        self.calls: list[list] = []
        self.types: list[list] = []
        self.rets: list[list] = []
        self.exports: list[list] = []
        self.other = 0
        self.surface = Surface()

    def result(self) -> dict:
        entries, sql, mounts = self.surface.result()
        return {
            "defs": self.defs,
            "binds": self.binds,
            "calls": self.calls,
            "types": self.types,
            "rets": self.rets,
            "exports": self.exports,
            "other": self.other,
            "entries": entries,
            "sql": sql,
            "mounts": mounts,
            "decls": self.surface.mounts.decls,
        }

    def params(self, fn, scope: str) -> None:
        """Type each plain parameter in ``scope``: by annotation, else unknown."""
        single = fn.child_by_field_name("parameter")
        group = fn.child_by_field_name("parameters")
        for p in ([single] if single is not None else []) + (
            group.named_children if group is not None else []
        ):
            if p.type == "assignment_pattern":
                p = p.child_by_field_name("left")
            pattern, ann = p, None
            if p is not None and p.type in ("required_parameter", "optional_parameter"):
                pattern, ann = p.child_by_field_name("pattern"), p.child_by_field_name("type")
            if pattern is not None and pattern.type == "identifier":
                ref = type_ref(ann) if ann is not None else None
                self.types.append([scope, text(pattern), *(ref or UNKNOWN)])

    def declarator(self, node, scope: str) -> None:
        """A variable's type, or the binding ``require`` makes of it."""
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if name is None:
            return
        spec = self._require(value)
        if spec is not None:
            self._bind_require(name, spec)
        elif name.type == "identifier":
            ann = node.child_by_field_name("type")
            ref = (type_ref(ann) if ann is not None else None) or value_ref(value)
            self.types.append([scope, text(name), *(ref or UNKNOWN)])

    @staticmethod
    def _require(value) -> str | None:
        if value is None or value.type != "call_expression":
            return None
        fn, args = value.child_by_field_name("function"), value.child_by_field_name("arguments")
        if fn is None or fn.type != "identifier" or text(fn) != "require" or args is None:
            return None
        first = next(iter(args.named_children), None)
        return _spec(first) if first is not None and first.type == "string" else None

    def _bind_require(self, name, spec: str) -> None:
        if name.type == "identifier":
            self.binds.append(["i", text(name), spec])
            return
        if name.type != "object_pattern":
            return
        for p in name.named_children:
            if p.type == "shorthand_property_identifier_pattern":
                self.binds.append(["f", text(p), spec, text(p)])
            elif p.type == "pair_pattern":
                key, val = p.child_by_field_name("key"), p.child_by_field_name("value")
                if val is not None and val.type == "identifier" and key is not None:
                    self.binds.append(["f", text(val), spec, text(key)])

    def imports(self, node) -> None:
        spec = _spec(node.child_by_field_name("source"))
        for clause in node.named_children:
            if clause.type == "import_require_clause":  # import x = require("m")
                name = next((c for c in clause.named_children if c.type == "identifier"), None)
                src = clause.child_by_field_name("source")
                if name is not None and src is not None:
                    self.binds.append(["i", text(name), _spec(src)])
            if clause.type != "import_clause":
                continue
            for part in clause.named_children:
                if part.type == "identifier":
                    self.binds.append(["f", text(part), spec, "default"])
                elif part.type == "namespace_import":
                    name = next((c for c in part.named_children if c.type == "identifier"), None)
                    if name is not None:
                        self.binds.append(["i", text(name), spec])
                elif part.type == "named_imports":
                    for s in part.named_children:
                        self._import_specifier(s, spec)

    def _import_specifier(self, node, spec: str) -> None:
        if node.type != "import_specifier":
            return
        name, alias = node.child_by_field_name("name"), node.child_by_field_name("alias")
        self.binds.append(["f", text(alias or name), spec, text(name)])

    def export(self, node) -> None:
        src = node.child_by_field_name("source")
        spec = _spec(src) if src is not None else ""
        clause = next((c for c in node.named_children if c.type == "export_clause"), None)
        if clause is not None:
            for s in clause.named_children:
                name, alias = s.child_by_field_name("name"), s.child_by_field_name("alias")
                if name is not None:
                    self.exports.append([text(alias or name), spec, text(name)])
        elif src is not None:
            if not any(c.type == "namespace_export" for c in node.children):
                self.exports.append(["*", spec, ""])
        elif any(c.type == "default" for c in node.children):
            d = node.child_by_field_name("declaration") or node.child_by_field_name("value")
            if d is not None:
                name = text(d) if d.type == "identifier" else text(d.child_by_field_name("name"))
                if is_qual(name):
                    self.exports.append(["default", "", name])

    def call(self, node, scope: str, cls: str | None) -> None:
        is_new = node.type == "new_expression"
        fn = node.child_by_field_name("constructor" if is_new else "function")
        line = node.start_point[0] + 1
        if fn is not None and fn.type == "identifier":
            self.calls.append([scope, "n", text(fn), "", line])
            return
        obj = fn.child_by_field_name("object") if fn is not None else None
        if fn is None or fn.type != "member_expression" or obj is None:
            self.other += 1
            return
        prop = text(fn.child_by_field_name("property"))
        if obj.type == "this" and cls is not None:
            self.calls.append([scope, "s", cls, prop, line])
        elif obj.type == "identifier":
            self.calls.append([scope, "a", text(obj), prop, line])
        else:
            self.other += 1


def _def_of(node, in_class: str | None, alias: str | None) -> tuple[str | None, str]:
    """The name and kind of a definition this node opens, else (None, "")."""
    t = node.type
    if t in _DECLS:
        return _ident(node.child_by_field_name("name")), "function"
    if t in _EXPRS:
        return alias, "function"
    if t in _CLASSES:
        return _ident(node.child_by_field_name("name")), "class"
    if t == "method_definition" and in_class is not None:
        return _ident(node.child_by_field_name("name")), "method"
    return None, ""


def walk(tree) -> dict:
    """Definitions, bindings, call sites, types and exports, in source order."""
    rec = _Records()
    # Frame: node, qual of the innermost def ("" at module level), the class whose body
    # this node sits directly in, the class `this` means, and the name a function
    # expression takes from its variable.
    stack: list[Frame] = [(tree.root_node, "", None, None, None)]
    while stack:
        node, qual, in_class, cls, alias = stack.pop()
        t = node.type
        scope = qual or MODULE
        kids = node.children
        is_fn = t in _DECLS or t in _EXPRS or t == "method_definition"
        name, kind = _def_of(node, in_class, alias)
        if is_fn and not (name is None and t == "method_definition"):
            body_cls = (
                in_class if t == "method_definition" else (cls if t == "arrow_function" else None)
            )
            inner = f"{qual}.{name}" if name and qual else (name or qual)
            if name:
                rec.defs.append([inner, kind, node.start_point[0] + 1, node.end_point[0] + 1])
                ret = type_ref(node.child_by_field_name("return_type"))
                if ret is not None:
                    rec.rets.append([inner, *ret])
            rec.params(node, inner or MODULE)
            if name:
                rec.surface.mounts.params(node, inner)
            stack.extend((c, inner, None, body_cls, None) for c in reversed(kids))
            continue
        if name and kind == "class":
            inner = f"{qual}.{name}" if qual else name
            rec.defs.append([inner, "class", node.start_point[0] + 1, node.end_point[0] + 1])
            body = node.child_by_field_name("body")
            for c in reversed(kids):
                stack.append(
                    (c, inner, inner, inner, None) if c == body else (c, qual, None, cls, None)
                )
            continue
        if t == "variable_declarator":
            rec.surface.declarator(node, scope)
            value = node.child_by_field_name("value")
            var = _ident(node.child_by_field_name("name"))
            if value is not None and value.type in _EXPRS and var is not None:
                for c in reversed(kids):
                    stack.append((c, qual, None, cls, var if c == value else None))
                continue
            rec.declarator(node, scope)
        elif t == "import_statement":
            rec.imports(node)
        elif t == "export_statement":
            rec.export(node)
        elif t in ("call_expression", "new_expression"):
            rec.call(node, scope, cls)
            if t == "call_expression":
                rec.surface.call(node, scope, cls)
        elif t == "assignment_expression":
            exported = module_export(node)
            if exported:
                rec.exports.append(["default", "", exported])
        elif t == "class_body" and in_class is not None:
            rec.surface.class_body(node, in_class)
        member_of = in_class if t == "class_body" else None  # members of a named class
        stack.extend((c, qual, member_of, cls, None) for c in reversed(kids))
    return rec.result()
