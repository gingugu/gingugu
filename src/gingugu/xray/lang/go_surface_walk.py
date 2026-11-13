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
from . import go_surface_mounts as mnt
from . import go_surface_route as route
from .go_surface_route import args as _args
from .go_surface_route import chain as _chain
from .go_surface_route import txt as _txt

_LITS = ("interpreted_string_literal", "raw_string_literal")
_FUNCS = ("function_declaration", "method_declaration")
_WRAP_DEPTH = 4
_UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")
_Assigns = dict[str, list[tuple[object, bool]]]  # name -> (value node or None, is "+=")


def _line(n) -> int:
    return n.start_point[0] + 1


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
    """The operands of a ``+`` chain, flattened (a non-``+`` expression is its own).
    A loop, not recursion: a crafted thousand-term chain must not cost the language."""
    out, stack = [], [n]
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        if cur.type == "binary_expression" and any(c.type == "+" for c in cur.children):
            stack.extend((cur.child_by_field_name("right"), cur.child_by_field_name("left")))
        else:
            out.append(cur)
    return out


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


def _safe(n, assigns: _Assigns, memo: dict[str, bool]) -> bool:
    """Whether one part is known safe. ``memo`` holds each local's verdict, so a
    name is judged once per function (``x2 = x1 + x1`` doubling chains stay linear);
    a name still being judged reads False, so a cycle is unsafe."""
    if n is None:
        return False
    if n.type in _LITS or (n.type == "identifier" and _UPPER.match(_txt(n))):
        return True
    if _has_marker(n):
        return True
    if n.type != "identifier":
        return False
    name = _txt(n)
    if name in memo:
        return memo[name]
    found = assigns.get(name)
    if not found:
        return False
    memo[name] = False
    memo[name] = all(_safe(v, assigns, memo) for v, _ in found)
    return memo[name]


def _unsafe_built(n, assigns: _Assigns, memo: dict[str, bool]) -> bool:
    parts = _parts(n)
    return parts is not None and not all(_safe(p, assigns, memo) for p in parts)


def _flag(call, assigns: _Assigns) -> bool:
    method = _txt(call.child_by_field_name("function").child_by_field_name("field"))
    args = _args(call)
    i = 1 if method.endswith("Context") else 0
    if len(args) <= i:
        return False
    q, memo = args[i], {}
    try:
        if q.type == "identifier":
            return any(
                v is not None
                and (_unsafe_built(v, assigns, memo) or (c and not _safe(v, assigns, memo)))
                for v, c in assigns.get(_txt(q), ())
            )
        return _unsafe_built(q, assigns, memo)
    except RecursionError:  # a crafted alias chain: flag the call, never lose the language
        return True


def _wrapped(call) -> tuple | None:
    """The handler a wrapping call holds: its first argument, through at most 4
    nested calls. (form, x, y, start line, end line), or None."""
    for _ in range(_WRAP_DEPTH):
        a = _args(call)[:1]
        h = a[0] if a else None
        if h is None:
            return None
        if h.type == "identifier":
            return "n", _txt(h), "", 0, 0
        if h.type == "selector_expression":
            op = h.child_by_field_name("operand")
            if op is not None and op.type == "identifier":
                return "a", _txt(op), _txt(h.child_by_field_name("field")), 0, 0
            return None
        if h.type == "func_literal":
            return "i", "", "", _line(h), h.end_point[0] + 1
        if h.type != "call_expression":
            return None
        call = h
    return None


def _handlers(call, label: str, scope: str, env: route.Env) -> list[list]:
    args, line = _args(call), _line(call)
    if not route.is_route(label, args):
        return []
    method, path = route.entry_route(label, args, env)
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
    if last is not None and last.type == "call_expression":
        wrapped = _wrapped(last)
        if wrapped is not None:
            out.append([scope, label, *wrapped[:3], line, *wrapped[3:]])
    recv = mnt.recv_of(label, env)
    out = [r + [method, path, recv] for r in out]
    return [r for r in out if rec.is_entry(r, "go")]


def surface(root, pkg: str, scopes: dict) -> dict:
    """``{"entries": [...], "sql": [...], "mounts": [...]}`` in source order; ``scopes`` maps each
    function node's ``(start_byte, end_byte)`` to the qual the symbol walk gave it."""
    entries: list[list] = []
    sql: list[list] = []
    mounts: list[list] = []
    cache: dict[tuple, _Assigns] = {}
    lit_env: dict[tuple, route.Env] = {}  # chi closure span -> env its body runs under
    stack: list[tuple[object, str | None, object, route.Env]] = [(root, MODULE, None, {})]
    while stack:
        node, scope, fn, env = stack.pop()
        if node.type in _FUNCS:
            scope, fn, env = scopes.get((node.start_byte, node.end_byte)), node, {}
            mnt.bind_params(node, env)
            is_main = node.type == "function_declaration" and pkg == "main"
            if scope is not None and is_main and _txt(node.child_by_field_name("name")) == "main":
                entries.append([MODULE, "main", "d", "main", "", _line(node), 0, 0, "", "", ""])
        elif node.type == "func_literal":
            env = lit_env.pop((node.start_byte, node.end_byte), env)
        elif node.type in ("short_var_declaration", "assignment_statement", "var_spec"):
            route.bind(node, env)
        elif node.type == "call_expression" and scope is not None:
            scoped = route.literal_env(node, env)
            if scoped is not None:
                lit_env[scoped[0]] = scoped[1]
            mounts.extend(mnt.mounts(node, scope, env))
            f = node.child_by_field_name("function")
            if f is not None and f.type == "selector_expression":
                label = _chain(f)
                if label and cat.entry_kind("go", label):
                    entries.extend(_handlers(node, label, scope, env))
                if _txt(f.child_by_field_name("field")) in cat.SQL_METHODS["go"]:
                    key = (fn.start_byte, fn.end_byte) if fn is not None else ()
                    if key not in cache:
                        cache[key] = _assigns(fn) if fn is not None else {}
                    if _flag(node, cache[key]):
                        sql.append([scope, _line(node)])
        stack.extend((c, scope, fn, env) for c in reversed(node.children))
    return {"entries": entries, "sql": sql, "mounts": mounts}
