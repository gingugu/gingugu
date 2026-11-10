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
MAX_URLS = 8  # URLs one entry may expand to
MAX_POPS = 256  # chain steps walked per entry, whatever the fan-out above it
ANY = "*"

Ref = tuple[str, str, str]  # (path, scope, name)


class Mount(NamedTuple):
    parent: Ref | None  # None: a mount on a value this repo cannot name (still a prefix)
    prefix: str  # surface_route shape: literal, PREFIX, or ""
    child: Ref
    path: str  # where the mount call is
    line: int


class Index:
    """The mounts by child, built once: a lookup per hop instead of a scan of every
    mount (a hostile repo can hold thousands, and every entry asks)."""

    def __init__(self, mounts: list[Mount]) -> None:
        exact: dict[Ref, list[Mount]] = {}
        anywhere: dict[tuple[str, str], list[Mount]] = {}
        for m in mounts:
            if m.child[2] == ANY:
                anywhere.setdefault((m.child[0], m.child[1]), []).append(m)
            else:
                exact.setdefault(m.child, []).append(m)
        self._exact, self._any = exact, anywhere

    def parents(self, ref: Ref) -> list[Mount]:
        """The mounts of ``ref`` - by name, or as any router of its scope - in
        source order."""
        found = self._exact.get(ref, []) + self._any.get((ref[0], ref[1]), [])
        return sorted(found, key=lambda m: (m.path, m.line))


def _index(mounts: list[Mount] | Index) -> Index:
    return mounts if isinstance(mounts, Index) else Index(mounts)


def full(ref: Ref, mounts: list[Mount] | Index) -> tuple[str, int]:
    """(prefix, how many places mount ``ref``): the whole chain above it."""
    idx = _index(mounts)
    found = idx.parents(ref)
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
        above = idx.parents(m.parent)
        if not above:
            break
        m = above[0]
    prefix = ""
    for p in reversed(parts):
        prefix = sr.join(prefix, p) if p else prefix
    return prefix, len(found)


def prefixes(ref: Ref, mounts: list[Mount] | Index) -> list[str]:
    """Every prefix ``ref`` is served under - one per mount chain, first mounts
    first, at most ``MAX_URLS``; ``[""]`` when nothing mounts it. A router mounted
    at ``/v1``, ``/v2`` and ``/v3`` answers at all three: each is surface."""
    idx = _index(mounts)
    out: list[str] = []
    stack: list[tuple[Ref, str, frozenset, int]] = [(ref, "", frozenset({ref}), 0)]
    pops = 0
    while stack and len(out) < MAX_URLS and pops < MAX_POPS:
        cur, tail, seen, depth = stack.pop()
        pops += 1
        above = idx.parents(cur)
        if not above:
            out.append(tail)
            continue
        up = []
        for m in above:
            t = sr.join(m.prefix, tail) if m.prefix else tail
            if m.parent is None or m.parent in seen or depth + 1 >= MAX_DEPTH:
                out.append(t)  # the chain ends here
            else:
                up.append((m.parent, t, seen | {m.parent}, depth + 1))
        stack.extend(reversed(up))  # first mounts are popped first
    return out[:MAX_URLS]


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
