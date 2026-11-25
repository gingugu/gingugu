"""Short-lived tokens derived from an owner token, held in memory only.

In remote mode a machine's owner token never travels on an MCP request. The
proxy trades it at ``POST /token/derive`` for one of these, carrying that
client's grant and home namespace. Only a SHA-256 of each token is kept, they
expire on their TTL, and a server restart drops them all - the proxy simply
derives again. Nothing here touches disk, so there is nothing to revoke; a
renewal hands back the token it replaces, so a proxy holds one, not two.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable

from .grants import Grant

MAX_TTL = 3600
DEFAULT_TTL = 3600
# A proxy derives one per refresh and hands back the one it replaces. Owners may fill the
# whole cap; tokens derived by scoped tokens (crew mates) only half of it.
MAX_LIVE = 256
SCOPED_SHARE = MAX_LIVE // 2  # the rest is the owners' headroom


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DerivedTokens:
    """An in-memory map of token hash -> (grant, expiry, deriver).

    ``deriver`` is the hash of the token that derived it (None when the caller
    gave none): only that same caller may replace it.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[Grant, float, str | None]] = {}

    def _prune(self, now: float) -> None:
        for key in [k for k, (_, exp, _by) in self._entries.items() if exp <= now]:
            del self._entries[key]

    def mint(
        self,
        grant: Grant,
        ttl: int,
        *,
        per_parent: int | None = None,
        by: str | None = None,
        replaces: str | None = None,
    ) -> str:
        """A new token for ``grant`` valid ``ttl`` seconds; the plaintext is returned once.

        A token a scoped token derives (``grant.parent``) counts against
        ``per_parent`` for that parent, and all of them together against half
        the shared cap, so scoped clients can never lock the owners out.

        ``replaces`` is the token a renewing caller is done with. The caps are
        checked as if it were gone - a renewal never needs a spare slot - and it
        is dropped only once the new token is minted, and only when ``by`` (the
        deriving token's hash) derived it too. Anything else it names is ignored.
        """
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 0 < ttl <= MAX_TTL:
            raise ValueError(f"ttl must be an integer from 1 to {MAX_TTL} seconds")
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._prune(now)
            drop = self._replaceable(replaces, by)
            others = {k: v for k, v in self._entries.items() if k != drop}  # as if dropped
            if len(others) >= MAX_LIVE:
                raise OverflowError("too many live derived tokens")
            if grant.parent is not None:
                scoped = [g.parent for g, *_ in others.values() if g.parent is not None]
                if len(scoped) >= SCOPED_SHARE:
                    raise OverflowError("too many live tokens derived by scoped tokens")
                if per_parent is not None and scoped.count(grant.parent) >= per_parent:
                    raise OverflowError(f"too many live derived tokens for {grant.parent}")
            if drop is not None:  # only once the new token is sure: a refusal keeps the old
                del self._entries[drop]
            self._entries[_hash(token)] = (grant, now + ttl, by)
        return token

    def _replaceable(self, replaces: str | None, by: str | None) -> str | None:
        """The key of ``replaces`` when ``by`` derived it, else None. Lock held."""
        if not replaces or by is None:
            return None
        key = _hash(replaces)
        entry = self._entries.get(key)
        if entry is None or entry[2] is None or not hmac.compare_digest(entry[2], by):
            return None
        return key

    def resolve(self, token: str) -> Grant | None:
        """The grant ``token`` carries, or None when unknown or expired."""
        if not token:
            return None
        presented = _hash(token)
        with self._lock:
            now = self._clock()
            match = None
            for key, entry in self._entries.items():  # no early exit on a hit
                if hmac.compare_digest(key, presented):
                    match = entry
            if match is None or match[1] <= now:
                return None
            return match[0]

    def live(self) -> int:
        with self._lock:
            self._prune(self._clock())
            return len(self._entries)

    def live_grants(self) -> list[Grant]:
        """The grant of every unexpired token. Never the tokens themselves."""
        with self._lock:
            self._prune(self._clock())
            return [grant for grant, *_ in self._entries.values()]
