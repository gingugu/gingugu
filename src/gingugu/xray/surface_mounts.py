"""X7d: routes across files - a router mounted under a prefix somewhere else.

A route as one file spells it (``@router.get("/me")``) is only the tail of its
URL when another file mounts that router (``app.include_router(users.router,
prefix="/api")``, ``app.use("/api", router)``, ``r.Mount("/admin", admin())``,
``routes.Register(v1)``). Pure: refs and mounts in, the full prefix per ref out.

A ``Ref`` names a router value: ``(path, scope, name)``. ``name`` is a variable,
``#<i>`` the scope's i-th parameter, or ``*`` - any router of that scope (a
function that builds and returns one; a Django URLconf module, scope
``<module>``). Each language's linker resolves both sides of a mount to refs.

- A mount says: routes on ``child`` live under ``prefix`` on ``parent``.
- ``full(ref)`` is the prefix chain from the outermost mount down: a parent that
  is itself mounted contributes its own ``full`` first. A cycle or a chain past
  ``MAX_DEPTH`` stops where it is. A router mounted in several places takes the
  first mount by (path, line); ``mounts`` says how many there were.
- A Nest global prefix (``app.setGlobalPrefix("api")``) applies to every Nest
  controller route under that app's project directory.
"""

from __future__ import annotations

from typing import NamedTuple

from . import surface_route as sr

MAX_DEPTH = 16
ANY = "*"

Ref = tuple[str, str, str]  # (path, scope, name)


class Mount(NamedTuple):
    parent: Ref | None  # None: a mount on a value this repo cannot name (still a prefix)
    prefix: str  # surface_route shape: literal, PREFIX, or ""
    child: Ref
    path: str  # where the mount call is
    line: int


def _matches(child: Ref, ref: Ref) -> bool:
    if child == ref:
        return True
    if child[2] != ANY or child[0] != ref[0]:
        return False
    return child[1] == ref[1] or (child[1] == "<module>" and ref[1] == "<module>")


def _parents(ref: Ref, mounts: list[Mount]) -> list[Mount]:
    return sorted((m for m in mounts if _matches(m.child, ref)), key=lambda m: (m.path, m.line))


def full(ref: Ref, mounts: list[Mount]) -> tuple[str, int]:
    """(prefix, how many places mount ``ref``): the whole chain above it."""
    found = _parents(ref, mounts)
    if not found:
        return "", 0
    parts: list[str] = []
    seen = {ref}
    m = found[0]
    while True:
        parts.append(m.prefix)
        if m.parent is None or m.parent in seen or len(parts) >= MAX_DEPTH:
            break
        seen.add(m.parent)
        above = _parents(m.parent, mounts)
        if not above:
            break
        m = above[0]
    prefix = ""
    for p in reversed(parts):
        prefix = sr.join(prefix, p) if p else prefix
    return prefix, len(found)


def project_dir(path: str) -> str:
    """The app directory a Nest ``main.ts`` sets a global prefix for: the parent of
    its ``src`` directory, else its own directory ("" at the repo root)."""
    parts = path.split("/")[:-1]
    if parts and parts[-1] == "src":
        parts = parts[:-1]
    return "/".join(parts)


def under(path: str, directory: str) -> bool:
    return not directory or path.startswith(directory + "/")


def route(prefix: str, tail: str) -> str:
    """``tail`` (a route as its file spells it) under ``prefix``; keeps the leading
    slash the tail had. A tail that is not literal stays ``""``."""
    if not tail or not prefix:
        return tail
    out = sr.join(prefix, tail)
    if out and tail.startswith("/") and not out.startswith(("/", sr.PREFIX)):
        out = sr.clean("/" + out)
    return out
