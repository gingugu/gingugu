"""Python: parsed with ``ast``, resolved by where each module sits (``resolve``).

Entry points are ``__main__.py``, a ``__main__`` guard, and the targets of the
root ``pyproject.toml``'s scripts and entry points.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import PurePosixPath

from .. import symbols, symbols_walk
from ..model import Edge, FileInfo, LangResult
from ..resolve import KIND_RANK, ModuleIndex, resolve_records
from ..resolve_walk import is_main_guard, walk


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


def _is_rec(rec: object) -> bool:
    """One import record as ``resolve_walk.walk`` writes it, after a JSON round trip."""
    # isinstance before ``in``: a list or dict where a str belongs is unhashable.
    if not isinstance(rec, list) or len(rec) < 3 or not isinstance(rec[1], str):
        return False
    if rec[1] not in KIND_RANK:
        return False
    if rec[0] == "i":
        return len(rec) == 3 and isinstance(rec[2], str)
    return (
        rec[0] == "f"
        and len(rec) == 5
        and _is_int(rec[2])
        and isinstance(rec[3], str)
        and isinstance(rec[4], list)
        and all(isinstance(n, str) for n in rec[4])
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class PythonAdapter:
    name = "python"
    extensions = (".py",)
    manifests = ("pyproject.toml",)

    def parse(self, rel: str, data: bytes) -> dict:
        """What the bytes alone say: import records, dynamic calls, ``__main__`` guard.

        ``rel`` is unused on purpose: the answer is cached by content, so it may
        not depend on where the file sits."""
        try:
            tree = ast.parse(data)
        except (SyntaxError, ValueError, UnicodeDecodeError, RecursionError):
            return {
                "err": True,
                "main": False,
                "dyn": 0,
                "recs": [],
                "sym": symbols_walk.walk(ast.parse("")),
            }
        recs, dynamic = walk(tree)
        return {
            "err": False,
            "main": is_main_guard(tree),
            "dyn": dynamic,
            "recs": recs,
            "sym": symbols_walk.walk(tree),
        }

    @staticmethod
    def valid(raw: object) -> bool:
        """Whether ``raw`` has exactly the shape ``parse`` returns (a cache is untrusted)."""
        return (
            isinstance(raw, dict)
            and set(raw) == {"err", "main", "dyn", "recs", "sym"}
            and isinstance(raw["err"], bool)
            and isinstance(raw["main"], bool)
            and _is_int(raw["dyn"])
            and isinstance(raw["recs"], list)
            and all(_is_rec(r) for r in raw["recs"])
            and symbols_walk.valid(raw["sym"])
        )

    def extract(self, sources: dict[str, bytes], manifests: dict[str, bytes]) -> LangResult:
        return self.link({rel: self.parse(rel, data) for rel, data in sources.items()}, manifests)

    def link(self, raws: dict[str, dict], manifests: dict[str, bytes]) -> LangResult:
        out = LangResult()
        idx = ModuleIndex(list(raws))
        script_files = {idx.names[m] for m in _scripts(manifests, out) if m in idx.names}
        for rel, raw in raws.items():
            is_entry = rel in script_files or PurePosixPath(rel).name == "__main__.py"
            if raw["err"]:
                out.parse_errors.append(rel)
                out.files.append(FileInfo(rel, is_test(rel), is_entry, self.name))
                continue
            res = resolve_records(raw["recs"], raw["dyn"], rel, idx)
            out.unresolved += res.unresolved
            out.dynamic += res.dynamic
            out.external += res.external
            out.edges.extend(Edge(rel, dst, k, c, i) for dst, (k, c, i) in res.edges.items())
            entry = is_entry or raw["main"]
            out.files.append(FileInfo(rel, is_test(rel), entry, self.name))
        out.symbols = symbols.link({rel: raw["sym"] for rel, raw in raws.items()}, idx)
        return out


ADAPTER = PythonAdapter()
