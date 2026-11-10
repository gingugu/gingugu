"""X7d for TS/JS: entry records and built-SQL records, fed by the symbol walk.

``Surface`` sees every call, declarator and class body the symbol walk visits
and adds, in source order, the two records of ``surface_records``:

- entry: a route or registration call (``app.get("/x", h)``, ``server.tool(...)``)
  whose first argument is a string or template literal; each function-shaped
  argument after it is an inline handler (form "i"), and a name-shaped last
  argument is a handler by name ("n", "a", or "s" for ``this.y`` in a class). A
  NestJS decorator on a method is a "d" entry for that method.
- sql: a ``SQL_METHODS`` call whose first argument is a built string (a template
  with substitutions, or a ``+`` chain with a non-literal operand) with a part
  not known safe. A bare identifier is judged by its declarators in the same scope
  once the file is walked, so a later declaration is seen; a parameter is opaque.

Walks are loops, never recursion: a hostile file can nest as deep as it likes.
"""

from __future__ import annotations

import re

from .. import surface_catalog as cat
from ..symbols_walk import is_qual
from .ts_symbols_types import text

_FUNCS = ("arrow_function", "function_expression", "function")
_UPPER = re.compile(r"[A-Z][A-Z0-9_]*\Z")


def _chain(node) -> str | None:
    """``a.b.c`` (``this`` may head it) as a dotted string, else None."""
    parts: list[str] = []
    while node is not None and node.type == "member_expression":
        prop = node.child_by_field_name("property")
        if prop is None or prop.type != "property_identifier":
            return None
        parts.append(text(prop))
        node = node.child_by_field_name("object")
    if node is None or node.type not in ("identifier", "this"):
        return None
    parts.append(text(node))
    return ".".join(reversed(parts))


def _ident(name: str) -> bool:
    return is_qual(name) and "." not in name


def _has_question(node) -> bool:
    """Whether any string literal at or under ``node`` holds a "?"."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "string_fragment" and b"?" in (n.text or b""):
            return True
        stack.extend(n.children)
    return False


def _is_plus(node) -> bool:
    op = node.child_by_field_name("operator")
    return op is not None and text(op) == "+"


def _operands(node) -> list:
    """The leaves of a ``+`` chain (parentheses looked through), in order."""
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if n.type == "parenthesized_expression" and n.named_children:
            stack.append(n.named_children[0])
        elif n.type == "binary_expression" and _is_plus(n):
            stack.extend([n.child_by_field_name("right"), n.child_by_field_name("left")])
        else:
            out.append(n)
    return out


def _parts(node) -> list | None:
    """The non-literal parts of a built string, None when ``node`` is not built."""
    if node.type == "template_string":
        subs = [c for c in node.children if c.type == "template_substitution"]
        return [s.named_children[0] for s in subs if s.named_children] if subs else None
    if node.type == "binary_expression" and _is_plus(node):
        rest = [o for o in _operands(node) if o.type != "string"]
        return rest or None
    return None


class Surface:
    """Collects entry and SQL candidates during the walk; ``result`` settles them."""

    def __init__(self) -> None:
        self.entries: list[list] = []
        self._sql: list[tuple[int, str, int, object]] = []
        self._decls: dict[tuple[str, str], list] = {}

    def result(self) -> tuple[list[list], list[list]]:
        """The ``entries`` and ``sql`` records, once the whole file is walked."""
        flagged = [(pos, s, line) for pos, s, line, arg in self._sql if self._unsafe(arg, s)]
        return self.entries, [[s, line] for _, s, line in sorted(flagged)]

    def declarator(self, node, scope: str) -> None:
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if name is not None and name.type == "identifier":
            self._decls.setdefault((scope, text(name)), []).append(value)

    def class_body(self, node, cls: str) -> None:
        """NestJS-style decorators precede the method they decorate."""
        pending: list = []
        for c in node.children:
            if c.type == "decorator":
                pending.append(c)
            elif c.type == "method_definition":
                name = c.child_by_field_name("name")
                for d in pending + [x for x in c.children if x.type == "decorator"]:
                    label = self._deco_label(d)
                    if label and name is not None and _ident(text(name)):
                        line = d.start_point[0] + 1
                        qual = f"{cls}.{text(name)}"
                        self.entries.append([cls, label, "d", qual, "", line, 0, 0])
                pending = []
            elif c.is_named and c.type != "comment":
                pending = []

    @staticmethod
    def _deco_label(deco) -> str | None:
        expr = deco.named_children[0] if deco.named_children else None
        if expr is not None and expr.type == "call_expression":
            expr = expr.child_by_field_name("function")
        label = _chain(expr) if expr is not None else None
        return label if label and cat.entry_kind("typescript", label) else None

    def call(self, node, scope: str, cls: str | None) -> None:
        fn, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        if fn is None or args is None or args.type != "arguments":
            return  # a tagged template has a template_string for "arguments"
        line = node.start_point[0] + 1
        if fn.type == "member_expression":
            m = fn.child_by_field_name("property")
            if m is not None and text(m) in cat.SQL_METHODS["typescript"] and args.named_children:
                self._sql.append((node.start_byte, scope, line, args.named_children[0]))
        label = _chain(fn)
        if label and cat.entry_kind("typescript", label):
            self._entry(label, args.named_children, scope, cls, line)

    def _entry(self, label: str, args: list, scope: str, cls: str | None, line: int) -> None:
        args = [a for a in args if a.type != "comment"]
        if len(args) < 2 or args[0].type not in ("string", "template_string"):
            return
        for a in args[1:]:
            if a.type in _FUNCS:
                span = (a.start_point[0] + 1, a.end_point[0] + 1)
                self.entries.append([scope, label, "i", "", "", line, *span])
        ref = self._handler_ref(args[-1], cls)
        if ref is not None:
            self.entries.append([scope, label, *ref, line, 0, 0])

    @staticmethod
    def _handler_ref(node, cls: str | None) -> tuple[str, str, str] | None:
        if node.type == "identifier":
            return ("n", text(node), "") if _ident(text(node)) else None
        if node.type != "member_expression":
            return None
        obj, prop = node.child_by_field_name("object"), node.child_by_field_name("property")
        if obj is None or prop is None or prop.type != "property_identifier":
            return None
        y = text(prop)
        if not _ident(y):
            return None
        if obj.type == "identifier" and _ident(text(obj)):
            return "a", text(obj), y
        if obj.type == "this" and cls is not None:
            return "s", cls, y
        return None

    def _unsafe(self, arg, scope: str) -> bool:
        if arg.type == "identifier":
            name = text(arg)
            return any(
                v is not None and _parts(v) is not None and not self._safe(v, scope, {name})
                for v in self._decls.get((scope, name), [])
            )
        parts = _parts(arg)
        return parts is not None and not all(self._safe(p, scope, set()) for p in parts)

    def _safe(self, node, scope: str, seen: set[str]) -> bool:
        """Whether one part of a built string (or a built value) is known safe."""
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "parenthesized_expression" and n.named_children:
                stack.append(n.named_children[0])
            elif n.type == "string" or _has_question(n):
                continue
            elif n.type == "template_string" or (n.type == "binary_expression" and _is_plus(n)):
                stack.extend(_parts(n) or [])
            elif n.type == "identifier":
                if not (_UPPER.match(text(n)) or self._local_safe(text(n), scope, seen)):
                    return False
            elif n.type == "member_expression":
                prop = n.child_by_field_name("property")
                if prop is None or not _UPPER.match(text(prop)):
                    return False
            else:
                return False
        return True

    def _local_safe(self, name: str, scope: str, seen: set[str]) -> bool:
        decls = self._decls.get((scope, name))
        if not decls or name in seen:
            return False
        return all(v is not None and self._safe(v, scope, seen | {name}) for v in decls)
