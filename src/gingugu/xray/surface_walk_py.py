"""X7d: the Python half of the attack-surface walk - entry points and built SQL.

``symbols_walk.walk`` hands each decorated function and each call here as it
visits them; the result rides in the same cache record as the X17 symbols, in
the shape ``surface_records`` defines (``entries`` and ``sql``). Like the rest
of the walk it reads one file's bytes alone.

- A decorator that ``surface_catalog.entry_kind`` knows makes a ``"d"`` entry.
- A call to a registrar (``app.add_url_rule``, ``path``, ``set_defaults``: the
  catalogue's handler-taking names, never an HTTP verb) makes an entry for the
  first handler-shaped argument.
- A query method (``execute``, ``text``...) handed a string built at runtime from
  a part not known safe makes a ``sql`` record. A part is safe when it is a
  literal, an UPPER_CASE constant, anything holding a ``"?"`` literal (a
  placeholder list), a conditional whose branches are safe, or a local whose
  every assignment in the scope is safe (followed two levels deep).
"""

from __future__ import annotations

import ast

from . import surface_catalog as cat

MODULE = "<module>"
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DEPTH = 2  # how many names deep a local's assignments are followed
Assigns = dict[str, dict[str, list[ast.expr | None]]]


def dotted(expr: ast.expr) -> str | None:
    """``a.b.c`` for a Name/Attribute chain over a Name, else None."""
    names: list[str] = []
    while isinstance(expr, ast.Attribute):
        names.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    names.append(expr.id)
    return ".".join(reversed(names))


def _is_str(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Constant) and isinstance(expr.value, str)


def built_parts(expr: ast.expr) -> list[ast.expr] | None:
    """The interpolated parts of a string built at runtime; None: not built."""
    if isinstance(expr, ast.JoinedStr):
        parts = [v.value for v in expr.values if isinstance(v, ast.FormattedValue)]
        return parts or None
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        leaves: list[ast.expr] = []
        todo: list[ast.expr] = [expr]
        while todo:
            n = todo.pop()
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                todo.extend((n.right, n.left))
            else:
                leaves.append(n)
        return leaves
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Mod):
        if isinstance(expr.left, ast.Constant) and not _is_str(expr.left):
            return None
        right = expr.right
        if isinstance(right, ast.Tuple):
            return [expr.left, *right.elts]
        if isinstance(right, ast.Dict):
            return [expr.left, *(v for v in right.values if v is not None)]
        return [expr.left, right]
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        if expr.func.attr == "format" and _is_str(expr.func.value):
            args = [a.value if isinstance(a, ast.Starred) else a for a in expr.args]
            return [*args, *(k.value for k in expr.keywords)]
    return None


def _has_placeholder(expr: ast.expr) -> bool:
    return any(_is_str(n) and "?" in n.value for n in ast.walk(expr))  # type: ignore[attr-defined]


def _safe(expr: ast.expr, assigns: dict[str, list[ast.expr | None]], depth: int) -> bool:
    parts = built_parts(expr)
    if parts is not None:
        return all(_safe(p, assigns, depth) for p in parts)
    if isinstance(expr, ast.Constant):
        return True
    if isinstance(expr, ast.Name) and expr.id.isupper():
        return True
    if isinstance(expr, ast.Attribute) and expr.attr.isupper():
        return True
    if _has_placeholder(expr):
        return True
    if isinstance(expr, ast.IfExp):
        return _safe(expr.body, assigns, depth) and _safe(expr.orelse, assigns, depth)
    if isinstance(expr, ast.Name) and depth < _DEPTH:
        values = assigns.get(expr.id)
        return bool(values) and all(v is not None and _safe(v, assigns, depth + 1) for v in values)
    return False


def _unsafe_built(expr: ast.expr, assigns: dict[str, list[ast.expr | None]]) -> bool:
    parts = built_parts(expr)
    return parts is not None and not all(_safe(p, assigns, 0) for p in parts)


def collect_assigns(tree: ast.Module) -> Assigns:
    """Every value assigned to each name, per scope (keyed as the walk keys scopes).

    A name bound any other way (parameter, loop variable, unpacking) holds ``None``:
    unknown, so never safe."""
    found: Assigns = {}

    def add(key: str, name: str, value: ast.expr | None) -> None:
        found.setdefault(key, {}).setdefault(name, []).append(value)

    todo: list[tuple[ast.AST, str]] = [(tree, "")]
    while todo:
        node, qual = todo.pop()
        key = qual or MODULE
        if isinstance(node, (*_DEFS, ast.ClassDef)):
            inner = f"{qual}.{node.name}" if qual else node.name
            if not isinstance(node, ast.ClassDef):
                a = node.args
                for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                    if arg is not None:
                        add(inner, arg.arg, None)
            todo.extend((c, inner) for c in reversed(node.body))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    add(key, t.id, node.value)
                else:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            add(key, n.id, None)
            todo.append((node.value, qual))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            value: ast.expr | None = node.value
            if isinstance(node, ast.AugAssign):
                plus = isinstance(node.op, ast.Add)
                value = ast.BinOp(node.target, ast.Add(), node.value) if plus else None
            if node.value is not None:
                add(key, node.target.id, value)
                todo.append((node.value, qual))
        else:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                add(key, node.id, None)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                add(key, node.name, None)
            todo.extend((c, qual) for c in reversed(list(ast.iter_child_nodes(node))))
    return found


def _is_main_test(test: ast.expr) -> bool:
    """``__name__ == "__main__"``, either way round."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    sides = (test.left, test.comparators[0])
    if not isinstance(test.ops[0], ast.Eq):
        return False
    names = [s for s in sides if isinstance(s, ast.Name) and s.id == "__name__"]
    consts = [s for s in sides if isinstance(s, ast.Constant) and s.value == "__main__"]
    return len(names) == 1 and len(consts) == 1


class Surface:
    """Collects ``entries`` and ``sql`` as ``symbols_walk.walk`` visits the tree."""

    def __init__(self, tree: ast.Module) -> None:
        self.entries: list[list] = []
        self.sql: list[list] = []
        self._assigns = collect_assigns(tree)
        self._decorators: set[int] = set()
        for node in tree.body:  # `if __name__ == "__main__":` - a script's entry point
            if isinstance(node, ast.If) and _is_main_test(node.test):
                end = node.end_lineno or node.lineno
                self.entries.append(
                    [MODULE, "__main__", "i", "", "", node.lineno, node.lineno, end]
                )

    def definition(self, node: ast.AST, scope: str, name: str) -> None:
        """A def or class: its decorators are entries (functions only), never registrars."""
        for deco in getattr(node, "decorator_list", []):
            if isinstance(deco, ast.Call):
                self._decorators.add(id(deco))
            label = dotted(deco.func if isinstance(deco, ast.Call) else deco)
            if isinstance(node, _DEFS) and label and cat.entry_kind("python", label):
                self.entries.append([scope, label, "d", name, "", deco.lineno, 0, 0])

    def call(self, node: ast.Call, scope: str, cls: str | None) -> None:
        """A call: a registrar makes an entry, a query method may make a ``sql`` record."""
        if id(node) not in self._decorators:
            label = dotted(node.func)
            if label and cat.py_registrar(label):  # not d.get(k)
                self._registrar(node, label, scope, cls)
        if isinstance(node.func, ast.Attribute) and node.func.attr in cat.SQL_METHODS["python"]:
            if node.args and self._flagged(node.args[0], self._assigns.get(scope, {})):
                self.sql.append([scope, node.lineno])

    def _registrar(self, node: ast.Call, label: str, scope: str, cls: str | None) -> None:
        args = list(node.args)
        if cat.entry_kind("python", label) == "http":  # the path comes first, never the handler
            if "." not in label and not (args and _is_str(args[0])):
                return  # a bare `path(x)` is Django's only with a literal route
            args = args[1:]
        for arg in [*args, *(k.value for k in node.keywords)]:
            if isinstance(arg, ast.Name):
                form, x, y = "n", arg.id, ""
            elif isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
                base = arg.value.id
                if cls is not None and base in ("self", "cls"):
                    form, x, y = "s", cls, arg.attr
                else:
                    form, x, y = "a", base, arg.attr
            else:
                continue
            self.entries.append([scope, label, form, x, y, node.lineno, 0, 0])
            return

    @staticmethod
    def _flagged(first: ast.expr, assigns: dict[str, list[ast.expr | None]]) -> bool:
        try:
            if isinstance(first, ast.Name):
                return any(
                    v is not None and _unsafe_built(v, assigns) for v in assigns.get(first.id, [])
                )
            return _unsafe_built(first, assigns)
        except RecursionError:
            return True
