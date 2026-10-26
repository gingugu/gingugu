"""Extract a repo's Python import graph from its git working tree.

Runs on the client, because it reads the caller's disk: the brain never sees a
path, only the :meth:`Extraction.payload`. Nothing here imports or executes the
repo being analysed - files are read as bytes and parsed with ``ast``.

**Why the fingerprint hashes content, not git state.** ``git status`` reads
" M" for a file whether it was edited once or ten times, and an empty commit
moves HEAD without changing a line of code. Neither is a usable cache key. The
fingerprint is a hash of every ``.py`` file's bytes (plus ``pyproject.toml`` and
the extractor version), so it changes exactly when the answer could, and bumping
``EXTRACTOR_VERSION`` invalidates graphs built by older logic.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .resolve import ModuleIndex, file_edges
from .scan import XrayError as XrayError
from .scan import commit, load_pyproject, read_sources, toplevel

EXTRACTOR_VERSION: int = 1
MAX_FILE_BYTES: int = 2_000_000  # real source is far smaller; read at call time


@dataclass
class FileInfo:
    path: str
    is_test: bool
    is_entry: bool


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    certain: bool
    implicit: bool = False


@dataclass
class Extraction:
    root: str
    commit: str | None
    fingerprint: str
    files: list[FileInfo] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    unresolved: int = 0
    dynamic: int = 0
    external: int = 0
    parse_errors: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def payload(self) -> dict:
        """JSON-safe wire form. Omits ``root``: a client path never leaves the client."""
        return {
            "fingerprint": self.fingerprint,
            "extractor_version": EXTRACTOR_VERSION,
            "commit": self.commit,
            "files": [
                {"path": f.path, "is_test": f.is_test, "is_entry": f.is_entry} for f in self.files
            ],
            "edges": [
                {
                    "src": e.src,
                    "dst": e.dst,
                    "kind": e.kind,
                    "certain": e.certain,
                    "implicit": e.implicit,
                }
                for e in self.edges
            ],
            "unresolved": self.unresolved,
            "dynamic": self.dynamic,
            "external": self.external,
            "parse_errors": list(self.parse_errors),
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped],
        }


def _scan(root: Path) -> tuple[dict[str, bytes], list[tuple[str, str]], bytes | None, set[str]]:
    sources, skipped = read_sources(root, MAX_FILE_BYTES)
    pyproject, reason, scripts = load_pyproject(root)
    if reason:
        skipped.append(("pyproject.toml", reason))
    return sources, sorted(skipped), pyproject, scripts


def _fingerprint(
    sources: dict[str, bytes], skipped: list[tuple[str, str]], pyproject: bytes | None
) -> str:
    h = hashlib.sha256(f"xray-v{EXTRACTOR_VERSION}\n".encode())
    for path in sorted(sources):
        h.update(f"{path}\0{hashlib.sha256(sources[path]).hexdigest()}\n".encode())
    for path, reason in skipped:
        h.update(f"skip\0{path}\0{reason}\n".encode())
    pyp = hashlib.sha256(pyproject).hexdigest() if pyproject is not None else "-"
    h.update(f"pyproject\0{pyp}\n".encode())
    return h.hexdigest()


def fingerprint(path: str | Path) -> str:
    """Cheap cache key for a repo: reads and hashes bytes, parses no Python."""
    sources, skipped, pyproject, _ = _scan(toplevel(path))
    return _fingerprint(sources, skipped, pyproject)


def _is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        any(part in ("tests", "test") for part in p.parts)
        or (p.name.startswith("test_") and p.name.endswith(".py"))
        or p.name.endswith("_test.py")
        or p.name == "conftest.py"
    )


def _is_main_guard(tree: ast.Module) -> bool:
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


def extract(path: str | Path) -> Extraction:
    """Build the import graph for the git repo containing ``path``."""
    root = toplevel(path)
    sources, skipped, pyproject, scripts = _scan(root)
    idx = ModuleIndex(list(sources))
    script_files = {idx.names[m] for m in scripts if m in idx.names}

    out = Extraction(
        root=str(root),
        commit=commit(root),
        fingerprint=_fingerprint(sources, skipped, pyproject),
        skipped=skipped,
    )
    for rel, data in sources.items():
        is_entry = rel in script_files or PurePosixPath(rel).name == "__main__.py"
        try:
            tree = ast.parse(data, filename=rel)
        except (SyntaxError, ValueError, UnicodeDecodeError, RecursionError):
            out.parse_errors.append(rel)
            out.files.append(FileInfo(rel, _is_test(rel), is_entry))
            continue
        res = file_edges(tree, rel, idx)
        out.unresolved += res.unresolved
        out.dynamic += res.dynamic
        out.external += res.external
        out.edges.extend(Edge(rel, dst, k, c, i) for dst, (k, c, i) in res.edges.items())
        out.files.append(FileInfo(rel, _is_test(rel), is_entry or _is_main_guard(tree)))

    out.edges.sort(key=lambda e: (e.src, e.dst))
    out.parse_errors.sort()
    return out
