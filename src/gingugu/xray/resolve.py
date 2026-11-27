"""Import resolution: turn ``import`` statements into edges between repo files.

Resolution is purely path-based. Module names are derived from where a file
sits (``pkg/__init__.py`` is ``pkg``), from the repo root, ``src/`` or an
inferred source root such as ``api/``, never by importing anything, so the
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

from .resolve_walk import walk

# Most binding first: an import that runs at module load beats one that might.
KIND_RANK = {"module": 0, "conditional": 1, "lazy": 2, "type_checking": 3}
_SOURCE_ROOTS = ("src/", "")


def infer_roots(paths: list[str]) -> list[str]:
    """Directories a service runs from: 1-2 levels deep, outside any package, and
    directly holding a regular package. ``api/`` with ``api/core/__init__.py`` is
    one; ``src/`` is already explicit. Pure path logic, deterministic."""
    pkg_dirs = {p.rpartition("/")[0] + "/" for p in paths if p.rpartition("/")[2] == "__init__.py"}
    found = set()
    for d in pkg_dirs:
        root = d.rpartition("/")[0].rpartition("/")[0]  # the dir holding the package
        root = root + "/" if root else ""
        depth = root.count("/")
        if not 1 <= depth <= 2 or root == "src/":
            continue
        parts = root.split("/")[:-1]
        if any("/".join(parts[:i]) + "/" in pkg_dirs for i in range(1, depth + 1)):
            continue  # inside a package: its own imports are relative
        found.add(root)
    return sorted(found, key=lambda r: (r.count("/"), r))


class ModuleIndex:
    """Dotted module name -> repo-relative path, over the source roots.

    Roots are ``src/``, the repo root, then inferred roots (see ``infer_roots``),
    in that order, so the global index is unchanged when none is inferred. A file
    under an inferred root imports against that root first: ``view`` returns the
    index with its root ahead of the rest, which is how ``from core import x``
    in ``api/`` means ``api/core`` and not a sibling service's ``core``.
    """

    def __init__(self, paths: list[str], roots: tuple[str, ...] | None = None) -> None:
        self._paths = paths
        self._inferred = tuple(infer_roots(paths)) if roots is None else ()
        self.roots = (*_SOURCE_ROOTS, *self._inferred) if roots is None else roots
        self._views: dict[str, ModuleIndex] = {}
        self.names: dict[str, str] = {}
        self._primary: dict[str, str] = {}
        for root in self.roots:  # src/ first: it wins a name collision
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

    def view(self, src_path: str) -> ModuleIndex:
        """The index as seen from ``src_path``: its deepest inferred root first."""
        under = [r for r in self._inferred if src_path.startswith(r)]
        if not under:
            return self
        root = max(under, key=lambda r: r.count("/"))
        if root not in self._views:
            order = (root, *[r for r in self.roots if r != root])
            self._views[root] = ModuleIndex(self._paths, order)
        return self._views[root]

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


def _absolute(name: str, idx: ModuleIndex, kind: str, res: FileResult) -> None:
    if name.split(".")[0] not in idx.tops:
        res.external += 1
        return
    for dst, explicit in idx.prefixes(name):
        res.add(dst, kind, True, not explicit)
    if name not in idx.names and name not in idx.packages:
        res.unresolved += 1


def _from_base(level: int, module: str, src: str, idx: ModuleIndex) -> str | None:
    """Dotted base module of a ``from`` import, or None when it climbs past the root."""
    if level == 0:
        return module
    pkg = idx.package_of(src)
    parts = pkg.split(".") if pkg else []
    up = level - 1
    if up >= len(parts):  # no package at all, or climbing past the top one
        return None
    parts = parts[: len(parts) - up] + (module.split(".") if module else [])
    return ".".join(parts)


def _resolve_from(rec: list, src: str, idx: ModuleIndex, res: FileResult) -> None:
    kind, level, module, names = rec[1], rec[2], rec[3], rec[4]
    base = _from_base(level, module, src, idx)
    if base is None:
        res.unresolved += 1
        return
    if level == 0 and base.split(".")[0] not in idx.tops:
        res.external += 1
        return
    cands = [f"{base}.{a}" if base else a for a in names]
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


def resolve_records(recs: list[list], dynamic: int, src: str, idx: ModuleIndex) -> FileResult:
    """Apply a file's import records (``resolve_walk.walk``) to the module index."""
    idx = idx.view(src)
    res = FileResult()
    res.dynamic = dynamic
    for rec in recs:
        if rec[0] == "i":
            _absolute(rec[2], idx, rec[1], res)
        else:
            _resolve_from(rec, src, idx, res)
    res.edges.pop(src, None)
    return res


def file_edges(tree: ast.Module, src: str, idx: ModuleIndex) -> FileResult:
    """Walk one parsed file, tracking how each import is reached."""
    return resolve_records(*walk(tree), src, idx)
