"""Shared file/template helpers for the bootstrap package.

Extracted so ``global_rules`` can use them without importing the package
``__init__`` that imports it back — a cycle that otherwise only works by
accident of import ordering.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def read_template(name: str) -> str:
    """Read a packaged template from ``gingugu/bootstrap/templates``."""
    return (files("gingugu.bootstrap") / "templates" / name).read_text()


def safe_read(path: Path) -> str:
    """Read ``path``, returning "" instead of raising when it cannot be read."""
    try:
        return path.read_text()
    except OSError:
        return ""
