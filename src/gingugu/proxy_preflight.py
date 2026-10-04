"""Remote mode's startup gate: refuse before a single byte is relayed.

Kept apart from `proxy` so each file stays small; `proxy` re-exports both names.
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

from . import remote
from .handlers.fence import stdio_grant
from .remote import RemoteTarget


class ProxyRefused(Exception):
    """Remote mode cannot start. The message is user-facing."""


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
    if not remote._reachable(target.url, token):
        raise ProxyRefused(f"unreachable: {target.url}")
    parsed = urlparse(target.url)
    if parsed.scheme == "http" and parsed.hostname not in remote._LOOPBACK:
        # stderr, never stdout: stdout is the MCP transport.
        print(
            "gingugu: warning - plain http to the remote brain: tokens cross the network "
            "unencrypted",
            file=sys.stderr,
        )
    return token
