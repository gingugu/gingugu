"""X7d, Go: what each entry answers to - HTTP method and route (``surface_route``).

Pure helpers for ``go_surface_walk``; none recurses. A prefix environment is a
``{receiver chain: prefix}`` dict the walk keeps per function:

- ``g := r.Group("/v1", mw...)`` (also ``=`` and ``var g = ...``) binds
  ``g`` to ``join(prefix(r), "/v1")``; a non-literal path binds ``PREFIX`` joined
  the same way, so groups chain.
- chi ``r.Route("/x", func(p chi.Router) {...})`` binds ``p`` to ``join(prefix(r),
  "/x")`` inside the literal; ``r.Group(func(p chi.Router) {...})`` binds ``p`` to
  ``prefix(r)`` unchanged.
- Group closure ``R.Group("/x", func() {...}, mw...)`` (the literal is the second
  argument): inside the literal ``R`` carries ``join(prefix(R), "/x")`` and keeps its
  root-router tracking, so closures nest; outside, ``R`` is unchanged. With a first
  parameter, that parameter gets the prefix and root, and ``R`` keeps its own. A
  non-literal path joins ``PREFIX``; an empty one leaves the prefix. Nesting is
  tracked ``MAX_DEPTH`` deep; a deeper closure adds no prefix (bounded copying).
- An entry's route is its first argument when that is a string literal, else "";
  the receiver's prefix goes in front. An empty literal is the prefix itself (no
  prefix: ""). Every route passes ``surface_route.clean``.
- ``is_route``: a verb name (every ``_GO_LAST`` name but Handle/HandleFunc/Method/
  MethodFunc) whose first argument is a string literal that is neither empty nor
  ``/``-led, a call or a function literal registers nothing (``slog.Any("error",
  err)``); a name or ``x.y`` first argument keeps route "".
"""

from __future__ import annotations

from .. import surface_route as sr

_LITS = ("interpreted_string_literal", "raw_string_literal")
Env = dict[str, str]
MAX_DEPTH = 32  # group closures nested deeper than this add no prefix
_PATTERNS = frozenset(("Handle", "HandleFunc", "Method", "MethodFunc"))
NEW_ROUTER = frozenset(("chi.NewRouter", "gin.New", "gin.Default", "echo.New", "http.NewServeMux"))


def txt(n) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def args(call) -> list:
    a = call.child_by_field_name("arguments")
    return [c for c in a.named_children if c.type != "comment"] if a is not None else []


def chain(n) -> str | None:
    """``a.b.c`` for a selector chain over identifiers (or a lone identifier)."""
    parts: list[str] = []
    while n is not None and n.type == "selector_expression":
        parts.append(txt(n.child_by_field_name("field")))
        n = n.child_by_field_name("operand")
    if n is None or n.type != "identifier":
        return None
    return ".".join([txt(n), *reversed(parts)])


def root_of(name: str, env: Env) -> str:
    """The root a receiver name stands for: its alias target, else itself."""
    return env.get("@" + name, name)


def track(env: Env, name: str, root: str, prefix: str | None = None) -> None:
    """Mark ``name`` a tracked router rooted at ``root`` (see ``go_surface_mounts``)."""
    env["@" + name], env["^" + name] = root, ""
    if prefix is not None:
        env[name] = prefix


def literal(n) -> str | None:
    """The value of a Go string literal node; None when ``n`` is not one. An
    interpreted literal with an escape yields "" (safe: ``clean`` would drop it)."""
    if n is None or n.type not in _LITS:
        return None
    t = txt(n)
    if len(t) < 2:
        return ""
    if n.type == "interpreted_string_literal" and "\\" in t:
        return ""
    return t[1:-1]


def _callee(call) -> tuple[str, str] | None:
    """(receiver chain, method name) of ``recv.Name(...)``; None for anything else."""
    if call is None or call.type != "call_expression":
        return None
    f = call.child_by_field_name("function")
    if f is None or f.type != "selector_expression":
        return None
    recv = chain(f.child_by_field_name("operand"))
    return (recv, txt(f.child_by_field_name("field"))) if recv else None


def entry_route(label: str, call_args: list, env: Env) -> tuple[str, str]:
    """(method, route) of the registration ``label(args...)``."""
    path = literal(call_args[0]) if call_args else None
    last = label.rsplit(".", 1)[-1]
    second = literal(call_args[1]) if len(call_args) > 2 else None
    if last in ("HandleFunc", "Handle", "Method", "MethodFunc") and path and second is not None:
        # method first: chi Method/MethodFunc, gin Handle("GET", "/x", h)
        method, route, raw = (sr.methods([path]) if path.upper() == path else ""), second, second
    elif last in ("HandleFunc", "Handle"):
        method, route = sr.go_pattern(path) if path else ("", "")
        raw = path
    else:
        method, route = ("" if last == "Any" else sr.method_of(label)), path or ""
        raw = path
    recv = label.rpartition(".")[0]
    if not route and raw != "":  # not a literal route: nothing to prefix
        return method, ""
    return method, sr.join(env.get(recv, ""), route)  # "" literal: the prefix itself


def is_route(label: str, call_args: list) -> bool:
    """Whether a call to ``label`` can register a route (the verb gate)."""
    if label.rsplit(".", 1)[-1] in _PATTERNS or not call_args:
        return True
    first = call_args[0]
    path = literal(first)
    if path is not None:
        return path == "" or path.startswith("/")
    return first.type not in ("call_expression", "func_literal")


def _depth(env: Env) -> int:
    return int(env.get("!", "0"))


def _group_prefix(recv: str, call_args: list, env: Env) -> str:
    path = literal(call_args[0]) if call_args else None
    return sr.join(env.get(recv, ""), sr.PREFIX if path is None else path)


def _is_group(call) -> bool:
    c = _callee(call)
    return c is not None and c[1] == "Group" and not _lit_arg(args(call)[:1])


def _lit_arg(a: list):
    return a[0] if a and a[0].type == "func_literal" else None


def bind(node, env: Env) -> None:
    """Record (or forget) the names an assignment or ``var`` spec sets."""
    if node.type == "var_spec":
        names = list(node.children_by_field_name("name"))
        value = node.child_by_field_name("value")  # None for `var x int`
        vals = list(value.named_children) if value is not None else []
    else:
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        names = list(left.named_children) if left is not None else []
        vals = list(right.named_children) if right is not None else []
    for i, nm in enumerate(names):
        if nm.type != "identifier":
            continue
        name, val = txt(nm), vals[i] if len(vals) == len(names) else None
        for key in (name, "@" + name, "^" + name):
            env.pop(key, None)
        if _is_group(val):
            recv, _ = _callee(val)  # type: ignore[misc]
            track(env, name, root_of(recv, env), _group_prefix(recv, args(val), env))
        elif val is not None and val.type == "call_expression":
            if chain(val.child_by_field_name("function")) in NEW_ROUTER:
                track(env, name, name)


def literal_env(call, env: Env) -> tuple[tuple, Env] | None:
    """For a chi ``Route``/``Group`` call with a function literal: the literal's
    (start, end) key and the env its body runs under; None otherwise."""
    c = _callee(call)
    if c is None or c[0] is None or c[1] not in ("Route", "Group"):
        return None
    a = args(call)
    lit, prefix, spare = None, env.get(c[0], ""), False
    if c[1] == "Route":
        lit = a[1] if len(a) > 1 and a[1].type == "func_literal" else None
        prefix = _group_prefix(c[0], a, env) if lit is not None else prefix
    elif _lit_arg(a) is not None:
        lit = a[0]
    elif len(a) > 1 and a[1].type == "func_literal":  # gitea m.Group("/x", func() {...})
        lit, spare = a[1], True
        if _depth(env) < MAX_DEPTH:
            prefix = _group_prefix(c[0], a, env)
    if lit is None:
        return None
    inner = dict(env)
    params = lit.child_by_field_name("parameters")
    first = params.named_children[0] if params is not None and params.named_children else None
    names = list(first.children_by_field_name("name")) if first is not None else []
    if names:
        inner[txt(names[0])] = prefix or ""
        track(inner, txt(names[0]), root_of(c[0], env))
    elif spare:  # no parameter: the receiver itself carries the prefix inside
        track(inner, c[0], root_of(c[0], env), prefix)
    if spare:
        inner["!"] = str(_depth(env) + 1)
    return (lit.start_byte, lit.end_byte), inner
