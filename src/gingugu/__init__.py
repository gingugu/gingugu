"""Gingugu — a local, persistent memory system for AI coding assistants.

An MCP server backed by SQLite + FTS5 that gives an AI partner a real
long-term brain: structured, searchable, namespace-scoped memory.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("gingugu")
except PackageNotFoundError:  # not installed (e.g. running from a source tree)
    __version__ = "0.0.0+unknown"
