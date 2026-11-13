"""X7d for TS/JS: the method and route an entry answers to (``surface_route``).

Helpers for ``ts_surface_walk.Surface``: literal reading, the Express chained
route (``app.route(p).get(h).post(h)``) and the NestJS ``@Controller`` prefix.
All loops, never recursion; nothing raises on odd input.
"""

from __future__ import annotations

from .. import surface_catalog as cat
from .. import surface_route as sr
from .ts_symbols_types import text

_Calls = list[tuple[str, object]]  # (verb, call node), outermost first


def literal(node) -> str | None:
    """A string or substitution-free template's text, ``sr.clean``ed; ``""`` for
    any other node or an unsafe literal. None only when ``node`` is None."""
    if node is None:
        return None
    if node.type == "string" or (
        node.type == "template_string"
        and not any(c.type == "template_substitution" for c in node.children)
    ):
        raw = text(node)
        return sr.clean(raw[1:-1]) if len(raw) >= 2 else ""
    return ""


def rooted(route: str) -> str:
    """An HTTP route written without a leading ``/`` (koa-router
    ``post("documents.info")``) given one; ``""``, ``*`` and rooted routes as is."""
    if not route or route[0] in "/*":
        return route
    return sr.clean("/" + route)


def _last(node) -> str | None:
    """The last name of a ``recv`` expression (``app``, ``this.app`` -> ``app``)."""
    if node is None:
        return None
    if node.type == "identifier":
        return text(node)
    if node.type == "member_expression":
        prop = node.child_by_field_name("property")
        return text(prop) if prop is not None else None
    return None


def chained(node) -> tuple[str, str, _Calls] | None:
    """For the OUTERMOST call of ``recv.route(path).verb(h)...``: (recv, path,
    verb calls in source order); None when it is not that shape."""
    parent = node.parent
    if parent is not None and parent.type == "member_expression":
        if parent.child_by_field_name("object") == node:
            return None  # an inner link: the outermost call handles the chain
    found: _Calls = []
    cur = node
    while cur is not None and cur.type == "call_expression":
        fn = cur.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return None
        prop, obj = fn.child_by_field_name("property"), fn.child_by_field_name("object")
        name = text(prop) if prop is not None else ""
        args = cur.child_by_field_name("arguments")
        if name == "route" and found and obj is not None:
            recv = _last(obj)
            first = args.named_children[0] if args is not None and args.named_children else None
            if recv in cat.JS_RECEIVERS and first is not None:
                return recv, literal(first) or "", found[::-1]
            return None
        found.append((name, cur))
        cur = obj
    return None


def verb_entry(recv: str, verb: str) -> tuple[str, str] | None:
    """(label, method) for ``recv.verb`` when it is an HTTP entry, else None."""
    label = f"{recv}.{verb}"
    return (label, sr.method_of(label)) if cat.entry_kind("typescript", label) else None


def _deco_parts(deco) -> tuple[str, list]:
    """(name, args) of ``@Name(args)`` / ``@Name``; ("", []) when not that shape."""
    expr = deco.named_children[0] if deco.named_children else None
    args: list = []
    if expr is not None and expr.type == "call_expression":
        a = expr.child_by_field_name("arguments")
        args = [c for c in a.named_children if c.type != "comment"] if a is not None else []
        expr = expr.child_by_field_name("function")
    return (text(expr) if expr is not None and expr.type == "identifier" else ""), args


def _object_path(obj) -> str | None:
    """The ``path:`` value of ``{ path: 'x', ... }``; None when there is none."""
    for pair in obj.named_children:
        key = pair.child_by_field_name("key")
        if pair.type == "pair" and key is not None and text(key) in ("path", "'path'", '"path"'):
            return literal(pair.child_by_field_name("value")) or sr.PREFIX
    return None


def controller_prefix(body) -> str:
    """The ``@Controller`` prefix of the class whose body is ``body``: ``""`` when
    bare or absent, ``sr.PREFIX`` when it is not a readable literal."""
    cls = body.parent
    if cls is None:
        return ""
    holders = [cls] + (
        [cls.parent] if cls.parent is not None and cls.parent.type == "export_statement" else []
    )
    for h in holders:
        for d in h.children:
            if d.type != "decorator":
                continue
            name, args = _deco_parts(d)
            if name != "Controller" or not args:
                continue
            a = args[0]
            if a.type == "object":
                return _object_path(a) or ""
            return literal(a) or sr.PREFIX
    return ""


def nest_route(deco, prefix: str) -> str:
    """The route of a ``@Get(q)``-style decorator under ``prefix``."""
    _, args = _deco_parts(deco)
    q = ""
    if args:
        q = literal(args[0]) or ""
        if not q:
            return ""  # an argument that is not a safe literal: route unknown
    if prefix == sr.PREFIX:
        return sr.join(prefix, q)
    if not (prefix or q):
        return "/"
    joined = sr.join(prefix, q)  # "" when the two together are no clean route
    return sr.clean("/" + joined.lstrip("/")) if joined else ""
