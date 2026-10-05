"""Remote mode's startup gate: refuse before a single byte is relayed.

Kept apart from `proxy` so each file stays small; `proxy` re-exports both names.
"""

from __future__ import annotations

import sys
import time
from urllib.parse import urlparse

from . import remote
from .handlers.fence import stdio_grant
from .remote import RemoteTarget

# A brain on WiFi can lose the first packet after idle (radio power save, a cold
# `.local` lookup), and an MCP client never retries a server that exits at
# startup. So keep probing for a while - well inside Claude Code's 30s connect
# timeout even when the last probe runs its full 3s.
REACH_BUDGET_S = 15.0
FIRST_BACKOFF_S = 0.5
MAX_BACKOFF_S = 2.0

# Seams for the tests.
_monotonic = time.monotonic
_sleep = time.sleep


class ProxyRefused(Exception):
    """Remote mode cannot start. The message is user-facing."""


def _await_brain(url: str, token: str) -> bool:
    """Probe until the brain answers or the budget is spent. No probe starts
    after the deadline."""
    deadline = _monotonic() + REACH_BUDGET_S
    delay = FIRST_BACKOFF_S
    while True:
        if remote._reachable(url, token):
            return True
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return False
        _sleep(min(delay, remaining))
        if _monotonic() >= deadline:
            return False
        delay = min(delay * 2, MAX_BACKOFF_S)


def preflight(target: RemoteTarget, *, grant: str | None, credentials_enabled: bool) -> str:
    """Return the owner token, or raise ProxyRefused. Config refusals come first
    so they never touch the keychain or the network."""
    if grant is not None:  # set-but-blank is refused too, never widened to full
        try:
            stdio_grant(grant)  # same rules as stdio; the brain enforces it for real
        except ValueError as exc:
            raise ProxyRefused(f"MEMORY_GRANT: {exc}") from None
    if credentials_enabled and grant is not None:
        # The owner's proxy serves the vault locally; a scoped client never
        # gets it, here or on stdio.
        raise ProxyRefused(
            "a client with MEMORY_GRANT gets no credential vault. "
            "Set MEMORY_CREDENTIALS_ENABLED=false."
        )
    try:
        token = remote.token_for(target.url)
    except Exception as exc:  # noqa: BLE001 - any keychain failure is a refusal
        raise ProxyRefused(f"could not read the keychain ({type(exc).__name__})") from None
    if not token:
        raise ProxyRefused(f"no token in the keychain. Run: gingugu remote login {target.url}")
    if not _await_brain(target.url, token):
        raise ProxyRefused(f"unreachable after {REACH_BUDGET_S:g}s: {target.url}")
    parsed = urlparse(target.url)
    if parsed.scheme == "http" and parsed.hostname not in remote._LOOPBACK:
        # stderr, never stdout: stdout is the MCP transport.
        print(
            "gingugu: warning - plain http to the remote brain: tokens cross the network "
            "unencrypted",
            file=sys.stderr,
        )
    return token
