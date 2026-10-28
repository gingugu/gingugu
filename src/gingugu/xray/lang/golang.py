"""Go: parsed with tree-sitter, resolved through ``go.mod``; imports name packages.

A directory with a non-test file is a package node named by its import path
(module path + directory under the nearest ``go.mod``). An importer points at
the package (``module``), the package at its files (``member``), a file leans
on its siblings through the package (``package``, uncertain and implicit), and
a test file leans on its own package (``package``, certain).
"""

from __future__ import annotations

from pathlib import PurePosixPath

from ..model import PACKAGE, Edge, FileInfo, LangResult

_LANG = None
_IMPORTS = None
_PACKAGE = None
_PARSER = None


def _parsers():
    """Language and queries, built once; tree-sitter loads only for a Go repo."""
    global _LANG, _IMPORTS, _PACKAGE, _PARSER
    if _LANG is None:
        import tree_sitter as ts
        import tree_sitter_go as tsg

        _LANG = ts.Language(tsg.language())
        _PARSER = ts.Parser(_LANG)
        _IMPORTS = ts.Query(_LANG, "(import_spec path: (_) @p)")
        _PACKAGE = ts.Query(_LANG, "(package_clause (package_identifier) @n)")
    return _PARSER, _IMPORTS, _PACKAGE


MAX_MODULE = 256


def _valid(mod: str) -> bool:
    """A module path becomes a node name, and the brain refuses a payload whole
    over one bad name: printable, bounded, no leading slash, no backslash, no
    empty, ``.`` or ``..`` segment. Also what an agent reads in an overview."""
    return (
        len(mod) <= MAX_MODULE
        and mod.isprintable()
        and not mod.startswith("/")
        and "\\" not in mod
        and all(part not in ("", ".", "..") for part in mod.split("/"))
    )


def module_path(data: bytes) -> str | None:
    """The ``module`` directive of a go.mod (quoted or not, comments ignored)."""
    for line in data.decode("utf-8", errors="replace").splitlines():
        parts = line.split("//", 1)[0].split()
        if len(parts) >= 2 and parts[0] == "module":
            mod = parts[1].strip('"`')
            return mod if mod and _valid(mod) else None
    return None


def _nearest_module(directory: str, modules: dict[str, str | None]) -> tuple[str, str] | None:
    """(go.mod directory, module path) owning ``directory``; None without a module."""
    path = PurePosixPath(directory)
    for d in (path, *path.parents):
        key = "" if str(d) == "." else str(d)
        if key in modules:
            mod = modules[key]
            return (key, mod) if mod else None
    return None


def _import_path(root: str, mod: str, directory: str) -> str:
    rel = directory[len(root) :].strip("/") if root else directory
    return f"{mod}/{rel}" if rel else mod


def _under(path: str, mods: set[str]) -> bool:
    """Whether ``path`` is one of ``mods`` or below one, on a ``/`` boundary.
    Walks the path's own prefixes, so the cost does not grow with the repo."""
    while path:
        if path in mods:
            return True
        path = path.rpartition("/")[0]
    return False


def _parse(rel: str, data: bytes, out: LangResult) -> tuple[str, list[str]]:
    """The package clause name and the import paths of one file."""
    import tree_sitter as ts

    parser, imports_q, package_q = _parsers()
    root = parser.parse(data).root_node
    if root.has_error:
        out.parse_errors.append(rel)
    names = ts.QueryCursor(package_q).captures(root).get("n", [])
    pkg = names[0].text.decode("utf-8", errors="replace") if names else ""
    paths = [
        n.text.decode("utf-8", errors="replace")[1:-1]
        for n in ts.QueryCursor(imports_q).captures(root).get("p", [])
    ]
    return pkg, paths


class GoAdapter:
    name = "go"
    extensions = (".go",)
    manifests = ("go.mod",)

    def extract(self, sources: dict[str, bytes], manifests: dict[str, bytes]) -> LangResult:
        out = LangResult()
        modules: dict[str, str | None] = {}
        for mpath, data in manifests.items():
            parent = str(PurePosixPath(mpath).parent)
            modules["" if parent == "." else parent] = module_path(data)
            if modules["" if parent == "." else parent] is None:
                out.skipped.append((mpath, "malformed"))
        mod_paths = {m for m in modules.values() if m}

        # file -> (import path of its package or None, is_test)
        placed: dict[str, tuple[str | None, bool]] = {}
        members: dict[str, list[str]] = {}
        for rel in sources:
            test = rel.endswith("_test.go")
            parent = str(PurePosixPath(rel).parent)
            directory = "" if parent == "." else parent
            owner = _nearest_module(directory, modules)
            pkg = _import_path(owner[0], owner[1], directory) if owner else None
            if pkg is not None and pkg in sources:
                pkg = None  # a crafted module path naming a real file never shadows it
            placed[rel] = (pkg, test)
            if pkg and not test:
                members.setdefault(pkg, []).append(rel)

        raw: dict[str, tuple[str, list[str]]] = {}
        for rel, data in sources.items():
            raw[rel] = _parse(rel, data, out)
        for rel, (pkg_path, test) in placed.items():
            clause, imports = raw[rel]
            out.files.append(FileInfo(rel, test, clause == "main" and not test, self.name))
            if pkg_path is None:
                out.external += len(imports)
                continue
            dsts: dict[str, Edge] = {}
            own = pkg_path in members
            if test and own:
                dsts[pkg_path] = Edge(rel, pkg_path, "package", True)
            elif own and len(members[pkg_path]) >= 2:
                dsts[pkg_path] = Edge(rel, pkg_path, "package", False, True)
            for imp in imports:
                if imp in members:
                    dsts.setdefault(imp, Edge(rel, imp, "module", True))
                elif _under(imp, mod_paths):
                    out.unresolved += 1
                else:
                    out.external += 1
            out.edges.extend(dsts.values())
        for pkg_path, files in members.items():
            out.files.append(FileInfo(pkg_path, False, False, self.name, PACKAGE))
            out.edges.extend(Edge(pkg_path, f, "member", True) for f in files)
        return out


ADAPTER = GoAdapter()
