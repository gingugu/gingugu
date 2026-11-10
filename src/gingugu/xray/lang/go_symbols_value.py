"""X7d, Go: which router value a name is, for ``surface_link.Collector`` (``Value``).

Only form ``"n"`` is meaningful in Go: a name ``x`` bound in the scope is
``(rel, scope, x)``; otherwise a package-level ``var`` of the same package (any
file in that directory) is ``(that file, "<module>", x)``; otherwise None. A name
two package-level declarations disagree on, or that two files both declare, is
not guessed at.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import surface_mounts as sm
from ..symbols_walk import MODULE

Key = tuple[str, bool]


def value_resolver(
    local: dict[str, dict], key: dict[str, Key], pvars: dict[Key, dict[str, list]], chain: Callable
) -> Callable[[str, str, str, str, str], sm.Ref | None]:
    """``value(rel, scope, form, x, y)`` over the linker's indexes (``chain(k)`` lists
    the packages whose package-level names ``k`` sees)."""

    def value(rel: str, scope: str, form: str, x: str, y: str) -> sm.Ref | None:
        if form != "n" or not x:
            return None
        if scope != MODULE and (scope, x) in local.get(rel, {}):
            return (rel, scope, x)
        for c in chain(key[rel]):
            found = pvars.get(c, {}).get(x)
            if found:
                return (found[0][0], MODULE, x) if len(found) == 1 else None
        return None

    return value
