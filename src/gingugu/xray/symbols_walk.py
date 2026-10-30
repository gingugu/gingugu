"""The AST half of the symbol graph: one parsed file in, JSON-safe records out.

Like ``resolve_walk`` this depends on the file's bytes alone - never on where
the file sits or what else is in the repo - so the result is cached by content
and ``symbols.link`` resolves it against the repo later.

Records (lists, so a JSON round trip is the identity):

- def:  ``[qual, kind, line, end]``; kind is "function", "method" or "class".
- bind: ``["i", local, dotted]`` or ``["f", local, level, module, name]``.
- call: ``[caller, form, x, y, line]``; form "n" is ``x()``, "a" is ``x.y()``
  on a bare name, "s" is ``self.y()``/``cls.y()`` in a method, ``x`` being the
  class. Calls on anything else are only counted in ``other``.
- type: ``[scope, var, form, x, y]``; ``var`` holds an instance of class ``x``
  (form "n") or ``x.y`` (form "a").
"""

from __future__ import annotations

import ast

MODULE = "<module>"
_KINDS = {"function", "method", "class"}
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _str_at(rec: list, *idx: int) -> bool:
    return all(isinstance(rec[i], str) for i in idx)


def is_qual(name: object, module_ok: bool = False) -> bool:
    """A dotted Python identifier, as ``walk`` writes a qualified name (or the
    module caller). Names are rendered into the agent's context, and a cache that
    could hold a newline could hold an instruction."""
    if not isinstance(name, str):
        return False
    if module_ok and name == MODULE:
        return True
    return all(part.isidentifier() for part in name.split("."))


def _is_def(rec: object) -> bool:
    return (
        isinstance(rec, list)
        and len(rec) == 4
        and _str_at(rec, 0, 1)
        and is_qual(rec[0])
        and rec[1] in _KINDS
        and _is_int(rec[2])
        and _is_int(rec[3])
    )


def _is_bind(rec: object) -> bool:
    if not isinstance(rec, list) or not rec or not isinstance(rec[0], str):
        return False
    if rec[0] == "i":
        return len(rec) == 3 and _str_at(rec, 1, 2)
    return rec[0] == "f" and len(rec) == 5 and _str_at(rec, 1, 3, 4) and _is_int(rec[2])


def _is_call(rec: object) -> bool:
    return (
        isinstance(rec, list)
        and len(rec) == 5
        and _str_at(rec, 0, 1, 2, 3)
        and is_qual(rec[0], module_ok=True)
        and rec[1] in ("n", "a", "s")
        and _is_int(rec[4])
    )


def _is_type(rec: object) -> bool:
    return (
        isinstance(rec, list)
        and len(rec) == 5
        and _str_at(rec, 0, 1, 2, 3, 4)
        and is_qual(rec[0], module_ok=True)
        and rec[2] in ("n", "a")
    )


_CHECKS = {"defs": _is_def, "binds": _is_bind, "calls": _is_call, "types": _is_type}


def valid(raw: object) -> bool:
    """Whether ``raw`` is exactly the shape ``walk`` returns (the cache is untrusted)."""
    if not isinstance(raw, dict) or set(raw) != {*_CHECKS, "other"}:
        return False
    if not _is_int(raw["other"]):
        return False
    return all(isinstance(raw[k], list) and all(f(r) for r in raw[k]) for k, f in _CHECKS.items())


def _type_ref(expr: ast.expr | None) -> tuple[str, str, str] | None:
    """``(form, x, y)`` for an annotation naming a class, else None."""
    if isinstance(expr, ast.Name):
        return "n", expr.id, ""
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        return "a", expr.value.id, expr.attr
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        if expr.value.isidentifier():
            return "n", expr.value, ""
    return None


def _ctor(value: ast.expr | None) -> ast.expr | None:
    return value.func if isinstance(value, ast.Call) else None


def _arg_nodes(args: ast.arguments) -> list[ast.arg]:
    found = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    return found + [a for a in (args.vararg, args.kwarg) if a is not None]


def walk(tree: ast.Module) -> dict:
    """Definitions, bindings, call sites and instance types, in source order."""
    defs: list[list] = []
    binds: list[list] = []
    calls: list[list] = []
    types: list[list] = []
    other = 0

    def add_type(scope: str, var: str, ref: tuple[str, str, str] | None) -> None:
        if ref is not None:
            types.append([scope, var, *ref])

    # Frame: node, qual of the innermost def/class ("" at module level), whether
    # that qual is a class body, and the class whose method we are in (or None).
    stack: list[tuple[ast.AST, str, bool, str | None]] = [(tree, "", False, None)]
    while stack:
        node, qual, in_class, cls = stack.pop()
        scope = qual or MODULE
        inner: list[tuple[ast.AST, str, bool, str | None]] = []
        outer: list[ast.AST] = []
        if isinstance(node, _DEFS) or isinstance(node, ast.ClassDef):
            name = f"{qual}.{node.name}" if qual else node.name
            is_class = isinstance(node, ast.ClassDef)
            kind = "class" if is_class else ("method" if in_class else "function")
            first = min([d.lineno for d in node.decorator_list] + [node.lineno])
            defs.append([name, kind, first, node.end_lineno or node.lineno])
            if is_class:
                outer = [*node.decorator_list, *node.bases, *node.keywords]
                inner = [(c, name, True, None) for c in node.body]
            else:
                outer = [*node.decorator_list, node.args, node.returns]
                body_cls = qual if in_class else cls
                inner = [(c, name, False, body_cls) for c in node.body]
                for a in _arg_nodes(node.args):
                    add_type(name, a.arg, _type_ref(a.annotation))
            stack.extend((c, qual, in_class, cls) for c in reversed(outer) if c is not None)
            stack.extend(reversed(inner))
            continue
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    binds.append(["i", a.asname, a.name])
                else:
                    binds.append(["i", a.name.split(".")[0], a.name.split(".")[0]])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    binds.append(["f", a.asname or a.name, node.level, node.module or "", a.name])
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                calls.append([scope, "n", fn.id, "", node.lineno])
            elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                base = fn.value.id
                if cls is not None and base in ("self", "cls"):
                    calls.append([scope, "s", cls, fn.attr, node.lineno])
                else:
                    calls.append([scope, "a", base, fn.attr, node.lineno])
            else:
                other += 1
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    add_type(scope, t.id, _type_ref(_ctor(node.value)))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            add_type(scope, node.target.id, _type_ref(node.annotation))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name):
                    add_type(scope, item.optional_vars.id, _type_ref(_ctor(item.context_expr)))
        children = list(ast.iter_child_nodes(node))
        stack.extend((c, qual, in_class, cls) for c in reversed(children))
    return {"defs": defs, "binds": binds, "calls": calls, "types": types, "other": other}
