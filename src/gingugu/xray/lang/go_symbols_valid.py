"""The shape check for ``go_symbols_walk`` records: the parse cache is untrusted.

Every name is rendered into the agent's context, so every qualified name must
pass ``symbols_walk.is_qual`` - a cache that could hold a newline could hold an
instruction. The record layouts are documented in ``go_symbols_walk``.
"""

from __future__ import annotations

from ..symbols_walk import is_qual

_KINDS = {"function", "method", "class"}
_FORMS = {"t", "c", "?"}
MAX_PATH = 512


def _int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def ident(v: object, empty: bool = False) -> bool:
    """One identifier (no dots); ``empty`` also lets ``""`` through."""
    if v == "":
        return empty
    return is_qual(v) and "." not in v


def _path(v: object) -> bool:
    return isinstance(v, str) and 0 < len(v) <= MAX_PATH and v.isprintable()


def is_bind(r: object) -> bool:
    ok = isinstance(r, list) and len(r) == 2 and _path(r[1])
    return ok and (r[0] in ("", ".") or ident(r[0]))


def is_def(r: object) -> bool:
    return (
        isinstance(r, list)
        and len(r) == 4
        and is_qual(r[0])
        and isinstance(r[1], str)  # before ``in``: a list there is unhashable
        and r[1] in _KINDS
        and _int(r[2])
        and _int(r[3])
    )


def is_call(r: object) -> bool:
    if not (isinstance(r, list) and len(r) == 5) or r[1] not in ("n", "a"):
        return False
    return is_qual(r[0], module_ok=True) and ident(r[2]) and ident(r[3], r[1] == "n") and _int(r[4])


def is_type(r: object) -> bool:
    if not (isinstance(r, list) and len(r) == 5 and isinstance(r[2], str)) or r[2] not in _FORMS:
        return False
    if r[2] == "?":
        return is_qual(r[0], module_ok=True) and ident(r[1]) and r[3] == "" and r[4] == ""
    return is_qual(r[0], module_ok=True) and ident(r[1]) and ident(r[3], True) and ident(r[4])


def is_result(r: object) -> bool:
    return (
        isinstance(r, list) and len(r) == 3 and is_qual(r[0]) and ident(r[1], True) and ident(r[2])
    )


_CHECKS = {
    "binds": is_bind,
    "defs": is_def,
    "calls": is_call,
    "types": is_type,
    "results": is_result,
}


def valid(raw: object) -> bool:
    """Whether ``raw`` is exactly the shape ``walk`` returns (the cache is untrusted)."""
    if not isinstance(raw, dict) or set(raw) != {*_CHECKS, "pkg", "other"}:
        return False
    if not _int(raw["other"]) or not (raw["pkg"] == "" or ident(raw["pkg"])):
        return False
    return all(isinstance(raw[k], list) and all(f(r) for r in raw[k]) for k, f in _CHECKS.items())
