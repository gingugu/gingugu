"""X7d: the (method, route) of a Python entry - the rules ``surface_route`` documents.

``surface_walk_py.Surface`` hands each decorator or registrar call here with the
entry's catalogue kind, and gets back the two strings the walk record ends with.
Everything is read from the one file; a value that is not a literal is ``""``.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from . import surface_route as sr

# These take `methods=[...]`; Flask, Starlette and FastAPI all default to GET.
_METHODS_KW = frozenset("route api_route add_url_rule add_api_route add_route Route".split())
_PATH_KW = ("path", "rule")
_ROUTERS = {"APIRouter": "prefix", "Blueprint": "url_prefix"}
Lookup = Callable[[str], "ast.expr | None"]


def _literal(node: ast.expr | None) -> str | None:
    """A string literal's value; None for anything else (or no node)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kw(call: ast.Call, *names: str) -> ast.expr | None:
    for k in call.keywords:
        if k.arg in names:
            return k.value
    return None


def _first(call: ast.Call, *names: str) -> ast.expr | None:
    """The first positional argument, else the first of keywords ``names``."""
    if call.args and not isinstance(call.args[0], ast.Starred):
        return call.args[0]
    return _kw(call, *names)


def _methods(call: ast.Call) -> str:
    node = _kw(call, "methods")
    if node is None:
        return "GET"
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return ""
    return sr.methods([_literal(e) for e in node.elts])


def _prefix(label: str, lookup: Lookup) -> str:
    """The same-file router prefix for ``label``'s receiver; ``""`` when none."""
    recv = label.rpartition(".")[0]
    if not recv or "." in recv:
        return ""
    made = lookup(recv)
    if not isinstance(made, ast.Call):
        return ""
    ctor = made.func.attr if isinstance(made.func, ast.Attribute) else getattr(made.func, "id", "")
    kw = _ROUTERS.get(ctor)
    node = _kw(made, kw) if kw else None
    if node is None:
        return ""
    return sr.clean(_literal(node)) or sr.PREFIX


def _http(label: str, node: ast.expr, lookup: Lookup) -> tuple[str, str]:
    last = label.rsplit(".", 1)[-1]
    if last in ("path", "re_path"):
        method = ""
    elif last in _METHODS_KW:
        method = _methods(node) if isinstance(node, ast.Call) else "GET"
    else:
        method = sr.method_of(label)
    if not isinstance(node, ast.Call):
        return method, ""
    raw = _literal(_first(node, *_PATH_KW))
    if raw is None or (raw and not sr.clean(raw)):
        return method, ""
    return method, sr.join(_prefix(label, lookup), raw)


def _named(node: ast.expr, fallback: str, *names: str) -> str:
    """The literal first argument or keyword, else the entry's own ``fallback``."""
    arg = _first(node, *names) if isinstance(node, ast.Call) else None
    return sr.clean(fallback) if arg is None else sr.clean(_literal(arg))


def resolve(kind: str, label: str, node: ast.expr, fn: str, lookup: Lookup) -> tuple[str, str]:
    """(method, route) for an entry of ``kind`` named ``label``. ``node`` is the
    decorator or registering call, ``fn`` the handler's own short name, ``lookup``
    maps a receiver name to the value last assigned to it in scope."""
    try:
        last = label.rsplit(".", 1)[-1]
        if kind == "http":
            return _http(label, node, lookup)
        if kind == "mcp":
            if last == "resource":
                return "", (
                    sr.clean(_literal(_first(node, "uri"))) if isinstance(node, ast.Call) else ""
                )
            if last == "add_tool":  # the first argument is the handler, not a name
                arg = _kw(node, "name") if isinstance(node, ast.Call) else None
                return "", sr.clean(fn if arg is None else _literal(arg))
            return "", _named(node, fn, "name")
        if kind == "cli" and last in ("command", "group"):
            return "", _named(node, fn.replace("_", "-"), "name")
    except (ValueError, RecursionError, AttributeError):
        pass
    return "", ""
