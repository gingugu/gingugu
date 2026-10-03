"""The hooks' transport to a remote brain.

A hook runs on the user's keystroke and holds the turn, so every call here has a
short timeout and every failure - no keychain token, a dead network, a non-200,
a body that is not a JSON object - is the same answer: ``None``. The caller then
stays quiet. There is deliberately no local fallback: with a remote brain on, a
stale local copy would answer questions about a different brain.

The token is the owner token ``gingugu remote login`` put in the keychain. It is
sent as a Bearer header and never logged, printed or put in an exception.
"""

from __future__ import annotations

from typing import Any

import httpx

from . import remote
from .remote import RemoteTarget

# Under the hook timeouts `gingugu init` writes (20 / 15 / 20 seconds), so a slow
# brain costs a quiet hook rather than a hook the harness has to kill.
RECALL_TIMEOUT_S = 10.0
TRIPWIRES_TIMEOUT_S = 5.0
TRIP_TIMEOUT_S = 2.0
WARMUP_TIMEOUT_S = 15.0


def target() -> RemoteTarget | None:
    """The brain in force, or None for local mode. A corrupt setting raises:
    each hook's ``main`` swallows it and exits quiet, never local."""
    return remote.active()


def _transport() -> httpx.BaseTransport | None:
    """The httpx transport; None means the default. A seam for tests."""
    return None


def post(target: RemoteTarget, path: str, body: dict, *, timeout: float) -> dict[str, Any] | None:
    """POST ``body`` to the brain; its JSON object on a 200, else None."""
    try:
        token = remote.token_for(target.url)
        if not token:
            return None
        with httpx.Client(transport=_transport(), timeout=timeout) as client:
            response = client.post(
                target.url + path, json=body, headers={"Authorization": f"Bearer {token}"}
            )
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception:  # noqa: BLE001 - a hook never breaks the turn
        return None
    return data if isinstance(data, dict) else None
