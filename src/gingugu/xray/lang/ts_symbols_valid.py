"""The exact shape check for TS/JS symbol records (the parse cache is untrusted).

Every qualified name goes into the agent's context, so each must be an
identifier chain; a record of any other shape sends the file to be parsed again.
"""

from __future__ import annotations

from .. import surface_records
from ..symbols_walk import is_qual

_KINDS = {"function", "method", "class"}


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _strs(rec: list, *idx: int) -> bool:
    return all(isinstance(rec[i], str) for i in idx)


def _def(r: list) -> bool:
    return (
        len(r) == 4
        and _strs(r, 0, 1)
        and is_qual(r[0])
        and r[1] in _KINDS
        and _int(r[2])
        and _int(r[3])
    )


def _bind(r: list) -> bool:
    if not r or not isinstance(r[0], str):
        return False
    if r[0] == "i":
        return len(r) == 3 and _strs(r, 1, 2)
    return r[0] == "f" and len(r) == 4 and _strs(r, 1, 2, 3)


def _call(r: list) -> bool:
    ok = len(r) == 5 and _strs(r, 0, 1, 2, 3) and is_qual(r[0], module_ok=True)
    return ok and r[1] in ("n", "a", "s") and _int(r[4])


def _type(r: list) -> bool:
    ok = len(r) == 5 and _strs(r, 0, 1, 2, 3, 4) and is_qual(r[0], module_ok=True)
    return ok and r[2] in ("n", "a", "p", "c", "d", "u")


def _ret(r: list) -> bool:
    return len(r) == 4 and _strs(r, 0, 1, 2, 3) and is_qual(r[0]) and r[1] in ("n", "a", "p")


def _export(r: list) -> bool:
    return len(r) == 3 and _strs(r, 0, 1, 2)


_CHECKS = {
    "defs": _def,
    "binds": _bind,
    "calls": _call,
    "types": _type,
    "rets": _ret,
    "exports": _export,
}


def valid(raw: object) -> bool:
    """Whether ``raw`` is exactly the shape ``ts_symbols_walk.walk`` returns."""
    keys = {*_CHECKS, "other", *surface_records.KEYS}
    if not isinstance(raw, dict) or set(raw) != keys or not _int(raw["other"]):
        return False
    return surface_records.valid(raw, "typescript") and all(
        isinstance(raw[k], list) and all(isinstance(r, list) and f(r) for r in raw[k])
        for k, f in _CHECKS.items()
    )
