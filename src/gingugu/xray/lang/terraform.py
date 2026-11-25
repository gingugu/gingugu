"""Terraform: the HCL graph of a repo's ``.tf`` files; see ``terraform_parse`` for the parse.

A directory of ``.tf`` files is one module with one namespace, so a reference
(``var.x``, ``local.x``, ``module.x``, ``data.T.N``, ``T.N``) leans on the file
in the same directory that declares it (``module``, certain). Each directory is
a package node ``terraform:<dir>`` reaching its files (``member``); a ``module``
block with a local ``source`` points at that directory's package. A registry,
git or URL source is external. A file in a directory no local source calls is
an entry (a root module). The adapter never runs ``terraform``.
"""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath

from ..model import PACKAGE, Edge, FileInfo, LangResult
from . import terraform_parse

_LOCAL = ("./", "../")


def _dir(rel: str) -> str:
    return str(PurePosixPath(rel).parent)


def _target(directory: str, source: str) -> str | None:
    """The repo directory a local ``source`` names; None when it leaves the repo."""
    target = posixpath.normpath(posixpath.join(directory, source))
    if target == ".." or target.startswith(("../", "/")):
        return None
    return target


class TerraformAdapter:
    name = "terraform"
    extensions = (".tf",)
    manifests: tuple[str, ...] = ()

    def parse(self, rel: str, data: bytes) -> dict:
        """What the bytes alone say; ``rel`` is unused (the answer is cached by content)."""
        return terraform_parse.parse(data)

    @staticmethod
    def valid(raw: object) -> bool:
        return terraform_parse.valid(raw)

    def extract(self, sources: dict[str, bytes], manifests: dict[str, bytes]) -> LangResult:
        return self.link({rel: self.parse(rel, data) for rel, data in sources.items()}, manifests)

    def link(self, raws: dict[str, dict], manifests: dict[str, bytes]) -> LangResult:
        out = LangResult()
        dirs: dict[str, list[str]] = {}
        for rel in sorted(raws):
            dirs.setdefault(_dir(rel), []).append(rel)
        declared: dict[str, dict[str, str]] = {d: {} for d in dirs}
        for d, files in dirs.items():
            for rel in files:
                for name in raws[rel]["decl"]:
                    declared[d].setdefault(name, rel)

        edges: dict[tuple[str, str], Edge] = {}

        def add(src: str, dst: str) -> None:
            edges.setdefault((src, dst), Edge(src, dst, "module", True))

        called: set[str] = set()
        for d, files in dirs.items():
            for rel in files:
                raw = raws[rel]
                for ref in raw["refs"]:
                    dst = declared[d].get(ref)
                    if dst is not None:
                        if dst != rel:
                            add(rel, dst)
                    elif ref.startswith(("var.", "local.", "module.", "data.")):
                        out.unresolved += 1
                for _name, source in raw["mods"]:
                    if not source.startswith(_LOCAL):
                        out.external += 1
                        continue
                    target = _target(d, source)
                    if target is None or target not in dirs:
                        out.unresolved += 1
                        continue
                    called.add(target)
                    add(rel, f"terraform:{target}")

        for d, files in dirs.items():
            entry = d not in called
            out.files.extend(FileInfo(rel, False, entry, self.name) for rel in files)
        for d, files in dirs.items():
            pkg = f"terraform:{d}"
            out.files.append(FileInfo(pkg, False, False, self.name, PACKAGE))
            out.edges.extend(Edge(pkg, rel, "member", True) for rel in files)
        out.edges.extend(edges.values())
        return out


ADAPTER = TerraformAdapter()
