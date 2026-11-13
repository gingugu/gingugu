"""X7d: the (method, route) of a Python entry - the rules ``surface_route`` documents.

``surface_walk_py.Surface`` hands each decorator or registrar call here with the
entry's catalogue kind, and gets back the two strings the walk record ends with.
Everything is read from the one file; a value that is not a literal is ``""``.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from . import surface_regex_py as regex
from . import surface_route as sr

# These take `methods=[...]`; Flask, Starlette and FastAPI all default to GET.
_METHODS_KW = frozenset("route api_route add_url_rule add_api_route add_route Route".split())
_PATH_KW = ("path", "rule")
MAX_PATHS = 16  # per registration: a crafted file cannot fan one call out into thousands
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


def _ctor(made: ast.Call) -> str:
    fn = made.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")


def _own(made: ast.Call) -> str | None:
    """The prefix the constructor call ``made`` sets itself; None when it sets none.
    ``*Router`` -> ``prefix=``, ``*Blueprint`` -> ``url_prefix=``, ``Namespace`` ->
    ``path=`` else ``/`` + its first positional literal."""
    ctor = _ctor(made)
    if ctor.endswith("Router"):
        node = _kw(made, "prefix")
    elif ctor.endswith("Blueprint"):
        node = _kw(made, "url_prefix")
    elif ctor.endswith("Namespace"):
        node = _kw(made, "path")
        if node is None and made.args and not isinstance(made.args[0], ast.Starred):
            lit = _literal(made.args[0])
            return sr.clean("/" + lit) or sr.PREFIX if lit else sr.PREFIX
    else:
        return None
    if node is None:
        return None
    return sr.clean(_literal(node)) or sr.PREFIX


def _prefix(label: str, lookup: Lookup) -> str:
    """The same-file router prefix for ``label``'s receiver; ``""`` when none.
    A ``*Api`` handed a name takes that name's own prefix (one hop, no more)."""
    recv = label.rpartition(".")[0]
    if not recv or "." in recv:
        return ""
    made = lookup(recv)
    if not isinstance(made, ast.Call):
        return ""
    if _ctor(made).endswith("Api"):
        if not made.args or not isinstance(made.args[0], ast.Name):
            return ""
        made = lookup(made.args[0].id)
        if not isinstance(made, ast.Call) or _ctor(made).endswith("Api"):
            return ""
    return _own(made) or ""


def paths(node: ast.Call, label: str, lookup: Lookup, skip: int = 0) -> list[str]:
    """The routes of every literal positional path argument after the first
    ``skip`` (at most ``MAX_PATHS``), the receiver's prefix joined in front."""
    out: list[str] = []
    try:
        prefix = _prefix(label, lookup)
        for arg in node.args[skip:]:
            raw = _literal(arg)
            if raw is None:
                continue
            out.append("" if raw and not sr.clean(raw) else sr.join(prefix, raw))
            if len(out) >= MAX_PATHS:
                break
    except (ValueError, RecursionError, AttributeError):
        return []
    return out


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
    if last == "re_path":
        found = regex.route(raw)
        return method, sr.join(_prefix(label, lookup), found) if found else ""
    if raw is None or (raw and not sr.clean(raw)):
        return method, ""
    if last == "path":  # Django routes are rooted: "login/" is /login/, "" is /
        raw = raw if raw.startswith("/") else "/" + raw
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
