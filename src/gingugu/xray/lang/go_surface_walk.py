"""X7d, Go: the entry and built-SQL records of one parsed file (``surface_records``).

A second pass over the tree ``go_symbols_walk`` already walked, so it reuses that
walk's names for functions (``scopes``: node span -> qual, ``None`` for a function
the symbol graph does not name). Rules:

- ``func main`` in package ``main`` is an entry (form "d", on its own line).
- A call whose callee is a selector chain over identifiers (``http.HandleFunc``,
  ``s.mux.HandleFunc``) that ``entry_kind`` knows registers handlers: every function
  literal argument after the first (form "i"), plus the last argument when it is a
  name (``h``) or ``x.y`` on a bare name.
- A query method (``SQL_METHODS["go"]``) handed a built string is flagged. Built: a
  ``+`` chain with a non-literal operand, or ``fmt.Sprintf`` with arguments after
  the format. A part is safe when it is a string literal, an UPPER_CASE name, an
  expression holding a literal with a ``?``, or a name every assignment of which
  (in the same function) is safe. A bare name is flagged when one of its
  assignments is built and unsafe; a parameter alone is opaque.
"""

from __future__ import annotations

import re

from .. import surface_catalog as cat
from .. import surface_records as rec
from ..symbols_walk import MODULE

_LITS = ("interpreted_string_literal", "raw_string_literal")
_FUNCS = ("function_declaration", "method_declaration")
_UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")
_Assigns = dict[str, list[tuple[object, bool]]]  # name -> (value node or None, is "+=")


def _txt(n) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _line(n) -> int:
    return n.start_point[0] + 1


def _args(call) -> list:
    a = call.child_by_field_name("arguments")
    return [c for c in a.named_children if c.type != "comment"] if a is not None else []


def _chain(n) -> str | None:
    """``a.b.c`` for a selector chain over identifiers (or a lone identifier)."""
    parts: list[str] = []
    while n is not None and n.type == "selector_expression":
        parts.append(_txt(n.child_by_field_name("field")))
        n = n.child_by_field_name("operand")
    if n is None or n.type != "identifier":
        return None
    return ".".join([_txt(n), *reversed(parts)])


def _names(n) -> list:
    return [c for c in n.named_children if c.type == "identifier"] if n is not None else []


def _vals(n) -> list:
    return list(n.named_children) if n is not None else []


def _assigns(fn) -> _Assigns:
    """Every assignment to a plain name anywhere inside the function ``fn``."""
    out: _Assigns = {}
    stack = [fn]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.type in ("short_var_declaration", "assignment_statement"):
            names = _names(n.child_by_field_name("left"))
            vals = _vals(n.child_by_field_name("right"))
            concat = any(c.type == "+=" for c in n.children)
        elif n.type == "var_spec":
            names = list(n.children_by_field_name("name"))
            vals = _vals(n.child_by_field_name("value"))
            concat = False
        else:
            continue
        for i, nm in enumerate(names):
            if nm.type == "identifier":
                paired = len(vals) == len(names)  # a lone multi-result call is not a safe value
                out.setdefault(_txt(nm), []).append((vals[i] if paired else None, concat))
    return out


def _has_marker(n) -> bool:
    """Whether the expression holds a string literal with a ``?`` placeholder."""
    stack = [n]
    while stack:
        c = stack.pop()
        if c.type in _LITS and "?" in _txt(c):
            return True
        stack.extend(c.children)
    return False


def _operands(n) -> list:
    """The operands of a ``+`` chain, flattened (a non-``+`` expression is its own)."""
    if n.type == "binary_expression" and any(c.type == "+" for c in n.children):
        return _operands(n.child_by_field_name("left")) + _operands(n.child_by_field_name("right"))
    return [n]


def _is_sprintf(n) -> bool:
    return (
        n.type == "call_expression" and _chain(n.child_by_field_name("function")) == "fmt.Sprintf"
    )


def _parts(n) -> list | None:
    """The non-literal parts of a built string; None when ``n`` is not built."""
    if _is_sprintf(n):
        return _args(n)[1:] or None
    ops = _operands(n)
    if len(ops) < 2:
        return None
    return [o for o in ops if o.type not in _LITS] or None


def _safe(n, assigns: _Assigns, seen: frozenset = frozenset()) -> bool:
    if n is None:
        return False
    if n.type in _LITS or (n.type == "identifier" and _UPPER.match(_txt(n))):
        return True
    if _has_marker(n):
        return True
    if n.type != "identifier":
        return False
    name = _txt(n)
    found = assigns.get(name)
    if not found or name in seen:
        return False
    return all(_safe(v, assigns, seen | {name}) for v, _ in found)


def _unsafe_built(n, assigns: _Assigns) -> bool:
    parts = _parts(n)
    return parts is not None and not all(_safe(p, assigns) for p in parts)


def _flag(call, assigns: _Assigns) -> bool:
    method = _txt(call.child_by_field_name("function").child_by_field_name("field"))
    args = _args(call)
    i = 1 if method.endswith("Context") else 0
    if len(args) <= i:
        return False
    q = args[i]
    if q.type == "identifier":
        return any(
            v is not None and (_unsafe_built(v, assigns) or (c and not _safe(v, assigns)))
            for v, c in assigns.get(_txt(q), ())
        )
    return _unsafe_built(q, assigns)


def _handlers(call, label: str, scope: str) -> list[list]:
    args, line = _args(call), _line(call)
    out: list[list] = []
    for a in args[1:]:
        if a.type == "func_literal":
            out.append([scope, label, "i", "", "", line, _line(a), a.end_point[0] + 1])
    last = args[-1] if len(args) > 1 else None
    if last is not None and last.type == "identifier":
        out.append([scope, label, "n", _txt(last), "", line, 0, 0])
    elif last is not None and last.type == "selector_expression":
        op = last.child_by_field_name("operand")
        if op is not None and op.type == "identifier":
            fld = _txt(last.child_by_field_name("field"))
            out.append([scope, label, "a", _txt(op), fld, line, 0, 0])
    return [r for r in out if rec.is_entry(r, "go")]


def surface(root, pkg: str, scopes: dict) -> dict:
    """``{"entries": [...], "sql": [...]}`` in source order; ``scopes`` maps each
    function node's ``(start_byte, end_byte)`` to the qual the symbol walk gave it."""
    entries: list[list] = []
    sql: list[list] = []
    cache: dict[tuple, _Assigns] = {}
    stack: list[tuple[object, str | None, object]] = [(root, MODULE, None)]
    while stack:
        node, scope, fn = stack.pop()
        if node.type in _FUNCS:
            scope, fn = scopes.get((node.start_byte, node.end_byte)), node
            is_main = node.type == "function_declaration" and pkg == "main"
            if scope is not None and is_main and _txt(node.child_by_field_name("name")) == "main":
                entries.append([MODULE, "main", "d", "main", "", _line(node), 0, 0])
        elif node.type == "call_expression" and scope is not None:
            f = node.child_by_field_name("function")
            if f is not None and f.type == "selector_expression":
                label = _chain(f)
                if label and cat.entry_kind("go", label):
                    entries.extend(_handlers(node, label, scope))
                if _txt(f.child_by_field_name("field")) in cat.SQL_METHODS["go"]:
                    key = (fn.start_byte, fn.end_byte) if fn is not None else ()
                    if key not in cache:
                        cache[key] = _assigns(fn) if fn is not None else {}
                    if _flag(node, cache[key]):
                        sql.append([scope, _line(node)])
        stack.extend((c, scope, fn) for c in reversed(node.children))
    return {"entries": entries, "sql": sql}
