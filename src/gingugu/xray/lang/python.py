"""Python: parsed with ``ast``, resolved by where each module sits (``resolve``).

Entry points are ``__main__.py``, a ``__main__`` guard, and the targets of the
root ``pyproject.toml``'s scripts and entry points.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import PurePosixPath

from ..model import Edge, FileInfo, LangResult
from ..resolve import ModuleIndex, file_edges


def _entry_targets(data: dict) -> set[str]:
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


def _scripts(manifests: dict[str, bytes], out: LangResult) -> set[str]:
    data = manifests.get("pyproject.toml")
    if data is None:
        return set()
    try:
        return _entry_targets(tomllib.loads(data.decode("utf-8")))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        out.skipped.append(("pyproject.toml", "malformed"))
        return set()


def is_test(path: str) -> bool:
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


class PythonAdapter:
    name = "python"
    extensions = (".py",)
    manifests = ("pyproject.toml",)

    def extract(self, sources: dict[str, bytes], manifests: dict[str, bytes]) -> LangResult:
        out = LangResult()
        idx = ModuleIndex(list(sources))
        script_files = {idx.names[m] for m in _scripts(manifests, out) if m in idx.names}
        for rel, data in sources.items():
            is_entry = rel in script_files or PurePosixPath(rel).name == "__main__.py"
            try:
                tree = ast.parse(data, filename=rel)
            except (SyntaxError, ValueError, UnicodeDecodeError, RecursionError):
                out.parse_errors.append(rel)
                out.files.append(FileInfo(rel, is_test(rel), is_entry, self.name))
                continue
            res = file_edges(tree, rel, idx)
            out.unresolved += res.unresolved
            out.dynamic += res.dynamic
            out.external += res.external
            out.edges.extend(Edge(rel, dst, k, c, i) for dst, (k, c, i) in res.edges.items())
            entry = is_entry or _is_main_guard(tree)
            out.files.append(FileInfo(rel, is_test(rel), entry, self.name))
        return out


ADAPTER = PythonAdapter()
