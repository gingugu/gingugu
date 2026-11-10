"""X7d, Go: receiver roots and mount records (``surface_records``), for ``go_surface_walk``.

The walk's per-function environment (``go_surface_route.Env``) also carries, under
keys no Go name can have:

- ``"@name"``: the root of ``name`` - the router a group alias ultimately hangs
  off (``v1 := r.Group("/v1")``: ``@v1`` is ``@r``'s root), ``#<i>`` when that root
  is the function's i-th parameter (receiver parameters are not counted), else the
  plain name (a local ``r := chi.NewRouter()``, a package-level router).
- ``"^name"``: ``name`` is a router or group the walk tracks (bound from
  ``Group``/``Route``/a router constructor, or a parameter typed like one).

Mount sites: chi ``P.Mount("/p", child)`` (a call -> ``cn``/``ca``, a name -> ``n``)
and a tracked router passed into a function, ``F(v1)`` / ``pkg.F(v1)`` (``pn``/``pa``).
Pure helpers; none recurses.
"""

from __future__ import annotations

import re

from .. import surface_records as rec
from .. import surface_route as sr
from .go_surface_route import Env, args, chain, literal, root_of, txt

_ROUTERISH = re.compile(r"Router|Group|Engine|Echo|Mux")


def _recv(root: str) -> str:
    return root if rec.is_recv(root) else ""


def recv_of(label: str, env: Env) -> str:
    """The recorded ``recv`` of an entry registered as ``label`` ("" for no receiver)."""
    head = label.rpartition(".")[0]
    if not head or "." in head:
        return ""
    return _recv(root_of(head, env))


def bind_params(fn, env: Env) -> None:
    """Root every parameter of ``fn`` as ``#<i>``; a router-typed one is tracked."""
    params = fn.child_by_field_name("parameters")
    i = 0
    for p in params.named_children if params is not None else []:
        if p.type not in ("parameter_declaration", "variadic_parameter_declaration"):
            continue
        names = list(p.children_by_field_name("name")) or [None]
        hinted = bool(_ROUTERISH.search(txt(p.child_by_field_name("type"))))
        for nm in names:
            if nm is not None and nm.type == "identifier":
                env["@" + txt(nm)] = f"#{i}"
                if hinted:
                    env["^" + txt(nm)] = ""
            i += 1


def _child(a) -> tuple[str, str, str] | None:
    """(form, x, y) of the expression mounted: ``f()``, ``pkg.F()`` or a name."""
    if a.type == "identifier":
        return "n", txt(a), ""
    if a.type != "call_expression":
        return None
    f = a.child_by_field_name("function")
    if f is not None and f.type == "identifier":
        return "cn", txt(f), ""
    if f is not None and f.type == "selector_expression":
        op = f.child_by_field_name("operand")
        if op is not None and op.type == "identifier":
            return "ca", txt(op), txt(f.child_by_field_name("field"))
    return None


def _callee(call) -> tuple[str, str, str] | None:
    """(form, x, y) of a call to ``F(...)`` / ``pkg.F(...)`` (a bare-name operand)."""
    f = call.child_by_field_name("function")
    if f is not None and f.type == "identifier":
        return "pn", txt(f), ""
    if f is not None and f.type == "selector_expression":
        op = f.child_by_field_name("operand")
        if op is not None and op.type == "identifier":
            return "pa", txt(op), txt(f.child_by_field_name("field"))
    return None


def mounts(call, scope: str, env: Env) -> list[list]:
    """The mount records of one call expression, in the shape ``is_mount`` checks."""
    line, a = call.start_point[0] + 1, args(call)
    f = call.child_by_field_name("function")
    out: list[list] = []
    if f is not None and f.type == "selector_expression":
        recv = chain(f.child_by_field_name("operand"))
        if recv and txt(f.child_by_field_name("field")) == "Mount" and len(a) == 2:
            child, lit = _child(a[1]), literal(a[0])
            if child is not None and lit is not None:
                prefix = sr.join(env.get(recv, ""), lit)
                out.append([scope, line, _recv(root_of(recv, env)), prefix, *child, 0])
            return [r for r in out if rec.is_mount(r)]
    callee = _callee(call)
    if callee is None or "^" + callee[1] in env:  # a method of a router: not a hand-off
        return out
    for i, arg in enumerate(a):
        if arg.type == "identifier" and "^" + txt(arg) in env:
            name = txt(arg)
            out.append([scope, line, _recv(root_of(name, env)), env.get(name, ""), *callee, i])
    return [r for r in out if rec.is_mount(r)]
