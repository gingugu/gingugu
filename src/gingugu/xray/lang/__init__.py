"""Language adapters: one per language family, each turning sources into a graph.

An adapter names the file extensions it parses and the manifest files it needs
to resolve imports (``go.mod``, ``tsconfig.json``), and turns their bytes into
a :class:`~gingugu.xray.model.LangResult`. ``extract`` does the reading, once,
for every adapter; an adapter never touches the disk, so it can only see what
the scan already vetted (symlinks, size, paths outside the repo).

Python stays on ``ast``: it is the exact grammar of the running interpreter.
Everything else parses with tree-sitter from pinned grammar wheels, imported
lazily inside the adapter so a Python-only repo never loads a parser.

Every adapter must parse, never execute: a manifest is read as data, and no
build tool, package manager or compiler of the analysed repo is ever run.
"""

from __future__ import annotations

from typing import Protocol

from ..model import LangResult


class Adapter(Protocol):
    name: str
    extensions: tuple[str, ...]
    # Basename patterns (fnmatch, e.g. "tsconfig*.json") read wherever they sit
    # in the repo, handed over by repo path.
    manifests: tuple[str, ...]

    def extract(self, sources: dict[str, bytes], manifests: dict[str, bytes]) -> LangResult: ...


def _registry() -> tuple[Adapter, ...]:
    from . import golang, python, typescript

    return (python.ADAPTER, typescript.ADAPTER, golang.ADAPTER)


ADAPTERS: tuple[Adapter, ...] = _registry()


def for_path(path: str) -> Adapter | None:
    """The adapter that parses ``path``, by extension; None when none does."""
    for adapter in ADAPTERS:
        if path.endswith(adapter.extensions):
            return adapter
    return None


def extensions() -> tuple[str, ...]:
    return tuple(ext for a in ADAPTERS for ext in a.extensions)


def manifests() -> tuple[str, ...]:
    return tuple(dict.fromkeys(m for a in ADAPTERS for m in a.manifests))
