"""X7d for TS/JS: Next.js app-router file routes.

A file ``route.<ext>`` under a directory named ``app`` answers the URL its path
spells, once per HTTP method it exports (``export async function GET``). The parse
cache is keyed by file content, so the two halves are split:

- ``method_exports`` (walk): the exported top-level functions named for a method
  (``surface_catalog.NEXT_VERBS``), in any file, whatever its path;
- ``route_of`` (link): the route a path spells, or None when the path is not a
  route file or a segment ``surface_route.clean`` refuses.

``route_of`` keeps the segments after the FIRST ``app`` segment: ``(group)`` and
``@slot`` dropped, ``[id]``, ``[...rest]`` and ``[[...rest]]`` become ``{id}``,
``{rest}``. Paths are untrusted: loops only, nothing raises.
"""

from __future__ import annotations

from .. import surface_catalog as cat
from .. import surface_route as sr
from .ts_symbols_types import text

_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts")
_ROUTE_FILES = frozenset("route" + e for e in _EXTS)
_FUNCS = ("arrow_function", "function_expression", "function")


def _segment(seg: str) -> str | None:
    """The route text of one path segment: "" when it adds none, None when refused."""
    if seg in ("", ".", ".."):
        return None
    if (seg[0] == "(" and seg[-1] == ")") or seg[0] == "@":
        return ""  # a route group or a parallel-route slot
    if seg[0] == "[" and seg[-1] == "]":
        name = seg.strip("[]").removeprefix("...")
        seg = "{" + name + "}" if name else ""
    return sr.clean(seg) or None


def route_of(rel: str) -> str | None:
    """The URL a Next.js app-router file answers, else None."""
    parts = rel.split("/") if isinstance(rel, str) else []
    if len(parts) < 2 or parts[-1] not in _ROUTE_FILES or "app" not in parts[:-1]:
        return None
    out = []
    for seg in parts[parts.index("app") + 1 : -1]:
        piece = _segment(seg)
        if piece is None:
            return None
        if piece:
            out.append(piece)
    return sr.clean("/" + "/".join(out)) or None


def method_exports(node) -> list[tuple[str, int]]:
    """(verb, line) for each method-named function an ``export_statement`` declares
    at the top level: ``export [async] function GET`` or ``export const GET = <fn>``."""
    decl = node.child_by_field_name("declaration")
    if decl is None:
        return []
    if decl.type == "function_declaration":
        name = decl.child_by_field_name("name")
        verb = text(name) if name is not None else ""
        return [(verb, decl.start_point[0] + 1)] if verb in cat.NEXT_VERBS else []
    found = []
    for d in decl.named_children if decl.type == "lexical_declaration" else []:
        name, value = d.child_by_field_name("name"), d.child_by_field_name("value")
        if d.type == "variable_declarator" and name is not None and value is not None:
            if name.type == "identifier" and value.type in _FUNCS and text(name) in cat.NEXT_VERBS:
                found.append((text(name), d.start_point[0] + 1))
    return found
