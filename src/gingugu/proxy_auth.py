"""The proxy's session token: derived, reused, renewed - and the ``/mcp`` auth on it.

A restarted brain has forgotten every derived token, so the next request comes
back 401. The brain refuses it before any tool runs, so sending the same
request again with a fresh token is safe - even a memory_store. Doing it per
request keeps one refusal from tearing down the link under every other call in
flight.

Every derive is a live token on the brain, which caps them, and a full cap
locks every machine out. So a token is renewed only once it has been accepted:
a brain that refuses each fresh token gets at most one derive per
``_RENEW_GAP`` seconds, not one per call.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import anyio
import httpx

from .proxy_session import derive

_RENEW_GAP = 10.0  # a token never accepted is renewed at most this often


class SessionTokens:
    """The live session token, reused across reconnects and refreshed at half its TTL."""

    def __init__(self, url: str, owner_token: str, derive_args: dict) -> None:
        self.url = url
        self.owner_token = owner_token
        self.derive_args = derive_args
        self.token: str | None = None
        self.expiry = 0.0
        self.ttl = 0
        self.derived_at = float("-inf")
        self.accepted = False  # the brain has taken the current token at least once
        self.reauth = False  # the brain refused the token: derive afresh
        self._renewing = anyio.Lock()

    async def derive(self) -> tuple[str, int]:
        token, expires_in = await derive(self.url, self.owner_token, **self.derive_args)
        now = anyio.current_time()
        self.token, self.expiry, self.ttl = token, now + expires_in, expires_in
        self.derived_at, self.accepted, self.reauth = now, False, False
        return token, expires_in

    def may_renew(self) -> bool:
        return self.accepted or anyio.current_time() - self.derived_at >= _RENEW_GAP

    def accept(self, token: str) -> None:
        if token == self.token:
            self.accepted = True

    async def renew(self, stale: str) -> None:
        """The brain refused ``stale``: derive once, however many requests saw it."""
        async with self._renewing:
            if self.token == stale and self.may_renew():
                await self.derive()

    async def current(self) -> tuple[str, int]:
        """The current token if it still has over half its life, else a fresh one."""
        left = self.expiry - anyio.current_time()
        if self.token and left > self.ttl / 2 and not (self.reauth and self.may_renew()):
            return self.token, int(left)
        return await self.derive()


class SessionAuth(httpx.Auth):
    """Bearer auth for the proxy's ``/mcp`` client: renew and retry once on a 401."""

    def __init__(self, tokens: SessionTokens) -> None:
        self.tokens = tokens

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        await request.aread()  # buffered: the body may be sent twice
        token = self.tokens.token or ""
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code != 401:
            self.tokens.accept(token)
            return
        await self.tokens.renew(token)
        if self.tokens.token == token:
            return  # not renewed: the 401 is the caller's
        request.headers["Authorization"] = f"Bearer {self.tokens.token}"
        response = yield request  # once: a second 401 is the caller's to handle
        if response.status_code != 401:
            self.tokens.accept(self.tokens.token or "")
