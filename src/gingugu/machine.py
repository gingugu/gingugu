"""This machine's name, and the credential summary that says whose vault it is.

The vault is per machine: a secret vaulted on one machine is in that machine's
keychain only. So ``memory_stats`` reporting ``total: 0`` on a machine that
never vaulted anything is expected, and without saying so it reads as data
loss. ``describe`` stamps the summary with the machine it counts and, at zero,
a note saying exactly that.

``name`` is the sanitised short hostname - the same name ``gingugu remote
login`` gives this machine's token by default, so it matches the board's
"on <machine>" unless that login passed ``--name``.
"""

from __future__ import annotations

import re
import socket

EMPTY_NOTE = (
    "the vault is per machine and nothing is vaulted on this one - "
    "expected, not data loss; credentials stored on another machine stay there"
)


def name() -> str:
    """This machine's name: lowercased short hostname, unsafe characters as ``-``."""
    return re.sub(r"[^a-z0-9._-]", "-", socket.gethostname().split(".")[0].lower())


def describe(health: dict) -> dict:
    """``health`` plus ``machine``, and ``note`` when it counts no credentials."""
    out = {**health, "machine": name()}
    if not health.get("total"):
        out["note"] = EMPTY_NOTE
    return out
