"""Import resolution: turn ``import`` statements into edges between repo files.

Resolution is purely path-based. Module names are derived from where a file
sits (``pkg/__init__.py`` is ``pkg``), never by importing anything, so the
analysed repo's code is only ever parsed. Directories without ``__init__.py``
still form packages (PEP 420), which is why names come from paths and not from
looking for initializers.

An edge's *certainty* records how sure we are that the import really targets
that file. ``from pkg import name`` may bind a submodule or merely a name
defined in ``pkg/__init__.py``; we cannot tell without executing, so the
submodule edge is a candidate (``certain=False``) and the package edge is not.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# Most binding first: an import that runs at module load beats one that might.
KIND_RANK = {"module": 0, "conditional": 1, "lazy": 2, "type_checking": 3}
_SOURCE_ROOTS = ("src/", "")
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


class ModuleIndex:
    """Dotted module name -> repo-relative path, for the repo root and ``src/``."""

    def __init__(self, paths: list[str]) -> None:
        self.names: dict[str, str] = {}
        self._primary: dict[str, str] = {}
        for root in _SOURCE_ROOTS:  # src/ first: it wins a name collision
            for path in paths:
                name = self._name(path, root)
                if name is None:
                    continue
                self.names.setdefault(name, path)
                self._primary.setdefault(path, name)
        # Directories holding modules are packages even with no __init__.py
        # (PEP 420). They have no file, so they are known names but never edges.
        self.packages = {
            ".".join(n.split(".")[:i]) for n in self.names for i in range(1, n.count(".") + 1)
        }
        self.tops = {n.split(".")[0] for n in self.names}

    @staticmethod
    def _name(path: str, root: str) -> str | None:
        if not path.startswith(root):
            return None
        parts = path[len(root) : -3].split("/")
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts) if parts else None

    def package_of(self, path: str) -> str:
        name = self._primary.get(path, "")
        if path.rpartition("/")[2] == "__init__.py":
            return name
        return name.rpartition(".")[0]

    def prefixes(self, name: str) -> list[tuple[str, bool]]:
        """Existing files for ``name`` and its parents, flagged True for ``name`` itself."""
        parts = name.split(".")
        out = []
        for i in range(len(parts)):
            path = self.names.get(".".join(parts[: i + 1]))
            if path is not None:
                out.append((path, i == len(parts) - 1))
        return out


@dataclass
class FileResult:
    edges: dict[str, tuple[str, bool, bool]] = field(
        default_factory=dict
    )  # dst -> (kind, certain, implicit)
    unresolved: int = 0
    dynamic: int = 0
    external: int = 0

    def add(self, dst: str, kind: str, certain: bool, implicit: bool = False) -> None:
        # Keep one real occurrence, never fields mixed from two: certain beats
        # candidate, then explicit beats implicit, then the most binding kind.
        old = self.edges.get(dst)
        new = (kind, certain, implicit)
        if old is None or self._key(new) < self._key(old):
            self.edges[dst] = new

    @staticmethod
    def _key(e: tuple[str, bool, bool]) -> tuple[bool, bool, int]:
        return (not e[1], e[2], KIND_RANK[e[0]])


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


def _absolute(name: str, idx: ModuleIndex, kind: str, res: FileResult) -> None:
    if name.split(".")[0] not in idx.tops:
        res.external += 1
        return
    for dst, explicit in idx.prefixes(name):
        res.add(dst, kind, True, not explicit)
    if name not in idx.names and name not in idx.packages:
        res.unresolved += 1


def _from_base(node: ast.ImportFrom, src: str, idx: ModuleIndex) -> str | None:
    """Dotted base module of a ``from`` import, or None when it climbs past the root."""
    if node.level == 0:
        return node.module or ""
    pkg = idx.package_of(src)
    parts = pkg.split(".") if pkg else []
    up = node.level - 1
    if up >= len(parts):  # no package at all, or climbing past the top one
        return None
    parts = parts[: len(parts) - up] + (node.module.split(".") if node.module else [])
    return ".".join(parts)


def _resolve_from(
    node: ast.ImportFrom, src: str, idx: ModuleIndex, kind: str, res: FileResult
) -> None:
    base = _from_base(node, src, idx)
    if base is None:
        res.unresolved += 1
        return
    if node.level == 0 and base.split(".")[0] not in idx.tops:
        res.external += 1
        return
    cands = [f"{base}.{a.name}" if base else a.name for a in node.names]
    # M is only the vehicle when every name is a submodule (each has its own
    # candidate edge); a name living in M, or `*`, makes the edge to M explicit.
    all_submodules = all(c in idx.names for c in cands)
    if base:
        for dst, is_base in idx.prefixes(base):
            res.add(dst, kind, True, not is_base or all_submodules)
        if base not in idx.names and base not in idx.packages:
            res.unresolved += 1
    for cand in cands:
        if cand in idx.names:
            res.add(idx.names[cand], kind, False)


def _is_dynamic_call(node: ast.Call) -> bool:
    fn = node.func
    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
    return name in _DYNAMIC


def file_edges(tree: ast.Module, src: str, idx: ModuleIndex) -> FileResult:
    """Walk one parsed file, tracking how each import is reached."""
    res = FileResult()
    stack: list[tuple[ast.AST, str]] = [(tree, "module")]
    while stack:
        node, ctx = stack.pop()
        if isinstance(node, ast.Import):
            for alias in node.names:
                _absolute(alias.name, idx, ctx, res)
        elif isinstance(node, ast.ImportFrom):
            _resolve_from(node, src, idx, ctx, res)
        elif isinstance(node, ast.Call) and _is_dynamic_call(node):
            res.dynamic += 1
        stack.extend(_child_ctx(node, ctx))
    res.edges.pop(src, None)
    return res
