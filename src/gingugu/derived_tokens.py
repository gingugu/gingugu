"""Short-lived tokens derived from an owner token, held in memory only.

In remote mode a machine's owner token never travels on an MCP request. The
proxy trades it at ``POST /token/derive`` for one of these, carrying that
client's grant and home namespace. Only a SHA-256 of each token is kept, they
expire on their TTL, and a server restart drops them all - the proxy simply
derives again. Nothing here touches disk, so there is nothing to revoke.
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
# Only owner tokens can mint, so this guards against a runaway client rather
# than an attacker: a proxy derives one per refresh, and the rest expire.
MAX_LIVE = 256


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DerivedTokens:
    """An in-memory map of token hash -> (grant, expiry)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[Grant, float]] = {}

    def _prune(self, now: float) -> None:
        for key in [k for k, (_, exp) in self._entries.items() if exp <= now]:
            del self._entries[key]

    def mint(self, grant: Grant, ttl: int) -> str:
        """A new token for ``grant`` valid ``ttl`` seconds; the plaintext is returned once."""
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 0 < ttl <= MAX_TTL:
            raise ValueError(f"ttl must be an integer from 1 to {MAX_TTL} seconds")
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._entries) >= MAX_LIVE:
                raise OverflowError("too many live derived tokens")
            self._entries[_hash(token)] = (grant, now + ttl)
        return token

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
