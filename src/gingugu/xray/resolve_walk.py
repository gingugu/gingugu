"""The AST half of import resolution: one parsed file in, JSON-safe records out.

Everything here depends on the file's bytes alone - never on where the file
sits or what else is in the repo - so its result can be cached by content. The
other half (``resolve``) applies those records to a ``ModuleIndex``.

A record is ``["i", ctx, name]`` for one ``import name`` alias, or
``["f", ctx, level, module, [names]]`` for ``from <level dots><module> import
names``. ``ctx`` is how the import is reached (see ``resolve.KIND_RANK``).
"""

from __future__ import annotations

import ast

_BRANCHING = (
    ast.If,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Match,
    getattr(ast, "TryStar", ast.Try),
)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_DYNAMIC = {"import_module", "__import__"}


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _child_ctx(node: ast.AST, ctx: str) -> list[tuple[ast.AST, str]]:
    """Children of ``node`` paired with the context they execute in."""
    if isinstance(node, ast.If) and _is_type_checking(node.test):
        # Typing-only wins even inside a def. The else branch runs exactly when
        # TYPE_CHECKING is false, so it is a real import (or stays lazy/typing-only).
        outer = ctx if ctx in ("type_checking", "lazy") else "conditional"
        out: list[tuple[ast.AST, str]] = [(node.test, ctx)]
        out += [(c, "type_checking") for c in node.body]
        out += [(c, outer) for c in node.orelse]
        return out
    if isinstance(node, _FUNCTIONS) and ctx != "type_checking":
        return [(c, "lazy") for c in ast.iter_child_nodes(node)]
    if ctx in ("lazy", "type_checking"):
        return [(c, ctx) for c in ast.iter_child_nodes(node)]
    if isinstance(node, _BRANCHING) and ctx == "module":
        ctx = "conditional"
    return [(c, ctx) for c in ast.iter_child_nodes(node)]


def _is_dynamic_call(node: ast.Call) -> bool:
    fn = node.func
    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
    return name in _DYNAMIC


def walk(tree: ast.Module) -> tuple[list[list], int]:
    """Import records in walk order, and the count of dynamic-import calls."""
    recs: list[list] = []
    dynamic = 0
    stack: list[tuple[ast.AST, str]] = [(tree, "module")]
    while stack:
        node, ctx = stack.pop()
        if isinstance(node, ast.Import):
            recs += [["i", ctx, alias.name] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            recs.append(["f", ctx, node.level, node.module or "", names])
        elif isinstance(node, ast.Call) and _is_dynamic_call(node):
            dynamic += 1
        stack.extend(_child_ctx(node, ctx))
    return recs, dynamic


def is_main_guard(tree: ast.Module) -> bool:
    """Whether the module has a top-level ``if __name__ == "__main__"``."""
    for node in tree.body:
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
            continue
        t = node.test
        if len(t.ops) != 1 or not isinstance(t.ops[0], ast.Eq):
            continue
        sides = [t.left, t.comparators[0]]
        has_name = any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
        has_main = any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)
        if has_name and has_main:
            return True
    return False
