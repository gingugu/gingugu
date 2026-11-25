"""X7d: an AWS Lambda handler is an entry point, found by its signature.

The runtime calls a module-level ``def handler(event, context)`` by the
``handler = "module.function"`` string in Terraform, SAM or serverless config,
so no decorator marks it. The walk records it under the label ``aws.lambda``,
which no decorator can produce (``x.lambda`` is a syntax error).
"""

from __future__ import annotations

import ast

LABEL = "aws.lambda"
_EVENT = frozenset({"event", "evt", "_event"})
_CONTEXT = frozenset({"context", "ctx", "_context", "_ctx", "_"})


def is_handler(node: ast.AST, scope: str, name: str) -> bool:
    """A module-level def with exactly two plain positional parameters, event then context."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if scope != "<module>" or "." in name:
        return False
    a = node.args
    if a.vararg or a.kwarg or a.kwonlyargs or a.defaults:
        return False
    params = [p.arg for p in (*a.posonlyargs, *a.args)]
    return len(params) == 2 and params[0] in _EVENT and params[1] in _CONTEXT


def record(node: ast.AST, scope: str, name: str) -> list | None:
    """The 11-field entry record for a Lambda handler, or None."""
    if not is_handler(node, scope, name):
        return None
    return [scope, LABEL, "d", name, "", node.lineno, 0, 0, "", "", ""]
