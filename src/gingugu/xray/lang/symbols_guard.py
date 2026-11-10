"""Contain a symbol linker's failure to that language's call graph.

The call graph (X17) is a lower bound layered on the import graph. A linker bug
on some repo shape the specs never saw - gin's thrice-declared value was one -
must cost that language's callers, not its import graph and not every other
language's. It is logged with its traceback, so it is contained, not hidden.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..symbols import Symbols

logger = logging.getLogger(__name__)


def contained(lang: str, link: Callable[..., Symbols], *args: Any) -> Symbols:
    """``link(*args)``, or an empty graph if it raises; the failure is logged."""
    try:
        return link(*args)
    except Exception:  # noqa: BLE001 - any linker bug, RecursionError included
        logger.warning("x-ray: %s symbol linking failed; no call graph for it", lang, exc_info=True)
        return Symbols()
