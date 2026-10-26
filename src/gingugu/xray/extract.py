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
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .resolve import ModuleIndex, file_edges

EXTRACTOR_VERSION: int = 1


class XrayError(Exception):
    """The path cannot be X-rayed (not a git repo, git missing, unreadable)."""


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
    skipped_symlinks: list[str] = field(default_factory=list)

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
                {"src": e.src, "dst": e.dst, "kind": e.kind, "certain": e.certain}
                for e in self.edges
            ],
            "unresolved": self.unresolved,
            "dynamic": self.dynamic,
            "external": self.external,
            "parse_errors": list(self.parse_errors),
            "skipped_symlinks": list(self.skipped_symlinks),
        }


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except OSError as exc:
        raise XrayError(f"cannot run git in {cwd}: {exc}") from exc


def _toplevel(path: str | Path) -> Path:
    proc = _git(Path(path), "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        raise XrayError(f"not a git repository: {path}")
    return Path(proc.stdout.decode().strip()).resolve()


def _commit(root: Path) -> str | None:
    proc = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    if proc.returncode != 0:
        return None  # unborn branch
    return proc.stdout.decode().strip() or None


def _read_sources(root: Path) -> tuple[dict[str, bytes], list[str]]:
    proc = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py")
    if proc.returncode != 0:
        raise XrayError(f"git ls-files failed: {proc.stderr.decode().strip()}")
    sources: dict[str, bytes] = {}
    symlinks: list[str] = []
    for rel in sorted({p for p in proc.stdout.decode().split("\0") if p}):
        full = root / rel
        if full.is_symlink():  # may point outside the repo: never follow
            symlinks.append(rel)
            continue
        if not full.is_file():  # --cached still lists a deleted tracked file
            continue
        try:
            sources[rel] = full.read_bytes()
        except OSError:
            continue
    return sources, symlinks


def _pyproject_bytes(root: Path) -> bytes | None:
    try:
        return (root / "pyproject.toml").read_bytes()
    except OSError:
        return None


def _fingerprint(sources: dict[str, bytes], symlinks: list[str], pyproject: bytes | None) -> str:
    h = hashlib.sha256(f"xray-v{EXTRACTOR_VERSION}\n".encode())
    for path in sorted(sources):
        h.update(f"{path}\0{hashlib.sha256(sources[path]).hexdigest()}\n".encode())
    for path in sorted(symlinks):
        h.update(f"symlink\0{path}\n".encode())
    pyp = hashlib.sha256(pyproject).hexdigest() if pyproject is not None else "-"
    h.update(f"pyproject\0{pyp}\n".encode())
    return h.hexdigest()


def fingerprint(path: str | Path) -> str:
    """Cheap cache key for a repo: reads and hashes bytes, parses nothing."""
    root = _toplevel(path)
    sources, symlinks = _read_sources(root)
    return _fingerprint(sources, symlinks, _pyproject_bytes(root))


def _is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        any(part in ("tests", "test") for part in p.parts)
        or (p.name.startswith("test_") and p.name.endswith(".py"))
        or p.name.endswith("_test.py")
        or p.name == "conftest.py"
    )


def _script_modules(pyproject: bytes | None) -> set[str]:
    if pyproject is None:
        return set()
    try:
        data = tomllib.loads(pyproject.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return set()
    project = data.get("project", {})
    targets: list[object] = []
    if isinstance(project, dict):
        for table in ("scripts", "gui-scripts"):
            group = project.get(table, {})
            targets += list(group.values()) if isinstance(group, dict) else []
        groups = project.get("entry-points", {})
        for group in groups.values() if isinstance(groups, dict) else []:
            targets += list(group.values()) if isinstance(group, dict) else []
    return {t.split(":")[0].strip() for t in targets if isinstance(t, str)}


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
    root = _toplevel(path)
    sources, symlinks = _read_sources(root)
    pyproject = _pyproject_bytes(root)
    idx = ModuleIndex(list(sources))
    script_files = {idx.names[m] for m in _script_modules(pyproject) if m in idx.names}

    out = Extraction(
        root=str(root), commit=_commit(root), fingerprint=_fingerprint(sources, symlinks, pyproject)
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
        out.edges.extend(Edge(rel, dst, k, c) for dst, (k, c) in res.edges.items())
        out.files.append(FileInfo(rel, _is_test(rel), is_entry or _is_main_guard(tree)))

    out.edges.sort(key=lambda e: (e.src, e.dst))
    out.parse_errors.sort()
    out.skipped_symlinks = sorted(symlinks)
    return out
