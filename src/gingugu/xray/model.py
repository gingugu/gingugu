"""The shapes every language adapter produces: files, edges, and what it could not read.

A node is a ``file`` or a ``package``. Packages exist for languages that import
a directory rather than a file (Go): the importer points at the package and the
package points at its files, so PageRank splits a package's rank across its
files instead of handing a 30-file package thirty shares of every importer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .symbols import Symbols

FILE = "file"
PACKAGE = "package"


@dataclass
class FileInfo:
    path: str
    is_test: bool
    is_entry: bool
    lang: str = "python"
    kind: str = FILE


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    certain: bool
    implicit: bool = False


@dataclass
class LangResult:
    """One adapter's share of an extraction."""

    files: list[FileInfo] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    unresolved: int = 0
    dynamic: int = 0
    external: int = 0
    parse_errors: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    symbols: Symbols = field(default_factory=Symbols)  # client-side only, never in a payload
