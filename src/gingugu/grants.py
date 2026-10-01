"""Per-request access grants for scoped `gingugu serve` tokens.

A grant maps namespace names to ``read`` or ``write`` (``*`` matches any
namespace). It is bound for the duration of one tool call, and the store's
chokepoints consult it, so the fence is the server's rather than an
instruction the caller is trusted to follow.

``current()`` returns None when nothing is restricted: stdio, background
passes, CLI commands, and a full-access token all run unfenced, exactly as
before. Only a scoped token over HTTP ever binds a restriction.

Anything the grant cannot see is reported as not found, never as forbidden,
so a scoped token cannot probe for what else lives in the brain.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

READ = "read"
WRITE = "write"
WILDCARD = "*"
LEVELS = (READ, WRITE)


class AccessDenied(Exception):
    """A scoped token reached for something outside its grant.

    The message is safe to return to the caller verbatim: it never names a
    namespace or memory the grant cannot already see.
    """


@dataclass(frozen=True)
class Grant:
    """What one token may touch: ``{namespace name: "read" | "write"}``."""

    name: str
    namespaces: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for ns, level in self.namespaces.items():
            if not ns or level not in LEVELS:
                raise ValueError(f"invalid grant entry {ns!r}: {level!r}")

    @property
    def is_full(self) -> bool:
        return self.namespaces.get(WILDCARD) == WRITE

    def level(self, namespace: str) -> str | None:
        """The strongest level granted on ``namespace``, or None."""
        found = {self.namespaces.get(namespace), self.namespaces.get(WILDCARD)}
        if WRITE in found:
            return WRITE
        return READ if READ in found else None

    def can_read(self, namespace: str) -> bool:
        return self.level(namespace) is not None

    def can_write(self, namespace: str) -> bool:
        return self.level(namespace) == WRITE


FULL = Grant("full", {WILDCARD: WRITE})


@dataclass(frozen=True)
class _Bound:
    grant: Grant
    conn: sqlite3.Connection


_current: ContextVar[_Bound | None] = ContextVar("gingugu_grant", default=None)


@contextmanager
def bind(grant: Grant, conn: sqlite3.Connection) -> Iterator[None]:
    """Fence everything inside the block to ``grant``.

    A full grant binds nothing: it is indistinguishable from no fence, and
    binding it would only cost the chokepoints a lookup per call.
    """
    if grant.is_full:
        yield
        return
    token = _current.set(_Bound(grant, conn))
    try:
        yield
    finally:
        _current.reset(token)


def current() -> Grant | None:
    """The restricting grant for this call, or None when unfenced."""
    bound = _current.get()
    return bound.grant if bound is not None else None


def _ids(level: str) -> list[str] | None:
    bound = _current.get()
    if bound is None:
        return None
    check = bound.grant.can_write if level == WRITE else bound.grant.can_read
    rows = bound.conn.execute("SELECT id, name FROM namespaces").fetchall()
    return [row[0] for row in rows if check(row[1])]


def readable_ids() -> list[str] | None:
    """Namespace ids this call may read; None means every namespace."""
    return _ids(READ)


def writable_ids() -> list[str] | None:
    """Namespace ids this call may write; None means every namespace."""
    return _ids(WRITE)


def can_read_id(namespace_id: str) -> bool:
    allowed = readable_ids()
    return allowed is None or namespace_id in allowed


def can_write_id(namespace_id: str) -> bool:
    allowed = writable_ids()
    return allowed is None or namespace_id in allowed


def can_read_name(name: str) -> bool:
    grant = current()
    return grant is None or grant.can_read(name)


def can_write_name(name: str) -> bool:
    grant = current()
    return grant is None or grant.can_write(name)


def require_write_id(namespace_id: str, what: str) -> None:
    """Raise unless this call may write to ``namespace_id``.

    Only reached for something the caller can already see, so naming
    ``what`` discloses nothing.
    """
    if not can_write_id(namespace_id):
        raise AccessDenied(f"{what} is read-only for this token")


def visible(ids: Iterable[str], namespace_of: Mapping[str, str]) -> list[str]:
    """Keep the ids whose namespace (looked up in ``namespace_of``) is readable."""
    allowed = readable_ids()
    if allowed is None:
        return list(ids)
    keep = set(allowed)
    return [i for i in ids if namespace_of.get(i) in keep]


def scope_clause(column: str) -> tuple[str | None, list[object]]:
    """A WHERE fragment confining ``column`` to readable namespaces.

    ``(None, [])`` when unfenced. A grant that can read nothing yields an
    always-false clause, never an empty ``IN ()`` - and never no clause.
    """
    allowed = readable_ids()
    if allowed is None:
        return None, []
    if not allowed:
        return "0", []
    placeholders = ", ".join("?" for _ in allowed)
    return f"{column} IN ({placeholders})", list(allowed)
