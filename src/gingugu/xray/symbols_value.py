"""X7d: the router value a Python expression is, for ``surface_link.Collector``.

``value(rel, scope, form, x, y)`` answers with a ``surface_mounts.Ref`` or None:

- ``"n"`` a name: assigned in ``scope`` or an enclosing one (the walk's ``vars``
  record names the scope) is ``(rel, that scope, name)``; else a from-import of a
  name of a repo module is ``(that module, "<module>", the name as defined there)``.
  Whether that name really is a variable there is not checked: a ref nothing
  mounts or registers on matches nothing.
- ``"a"`` ``x.y``: ``x`` an imported repo module is ``(its path, "<module>", y)``.
- ``"m"`` a dotted module: ``(its path, "<module>", "*")``.
"""

from __future__ import annotations

from collections.abc import Callable

from . import surface_mounts as sm
from .resolve import ModuleIndex, _from_base
from .surface_link import Value

MODULE = "<module>"
Bound = Callable[[str, list], "tuple | str | None"]


def _imported_name(rel: str, bind: list, idx: ModuleIndex) -> sm.Ref | None:
    """A from-import binding of a name inside a repo module, as that name's ref."""
    if bind[0] != "f":
        return None
    level, module, name = bind[2], bind[3], bind[4]
    base = _from_base(level, module, rel, idx)
    if base is None or (level == 0 and base.split(".")[0] not in idx.tops):
        return None
    if (f"{base}.{name}" if base else name) in idx.names:
        return None  # a submodule, not a value
    home = idx.names.get(base)
    return (home, MODULE, name) if home is not None else None


def make(rel: str, raw: dict, binds: dict, bound: Bound, idx: ModuleIndex) -> Value:
    """The ``value`` function for the file ``rel`` (its walk record ``raw``)."""
    homes = {(v[0], v[1]): v[2] for v in raw.get("vars", ())}

    def value(_rel: str, scope: str, form: str, x: str, y: str) -> sm.Ref | None:
        try:
            if form == "n":
                home = homes.get((scope, x))
                if home is not None:
                    return (rel, home, x)
                return _imported_name(rel, binds[x], idx) if x in binds else None
            if form == "a":
                target = bound(rel, binds[x]) if x in binds else None
                if isinstance(target, tuple) and target[0] == "mod":
                    return (target[1], MODULE, y)
                return None
            if form == "m":
                path = idx.names.get(x)
                return (path, MODULE, sm.ANY) if path else None
        except (KeyError, IndexError, TypeError):
            pass
        return None

    return value
