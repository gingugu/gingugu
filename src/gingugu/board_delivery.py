"""Whether a post's recipient can see it - the board's delivery receipt.

A post lives in a namespace and is read through that namespace's grant, so a
post addressed to a persona whose clients cannot read the namespace is stored
and never seen. The poster used to get a bare ``ok`` for it, which read as
delivered.

**A persona is not a token.** Grants belong to clients: each proxy derives a
short-lived token named for its persona, and one persona may run several, each
with its own grant. So the receipt answers from every client the brain knows
for that persona - live derived tokens and scoped tokens of that name - and has
three answers: visible (one of them can read the namespace), not visible (none
can), or unverified (none is known). Absence of a connected client is not
evidence the persona cannot read; it is no evidence at all.

The post is stored either way. Grants change, and refusing a post on what the
brain happens to see right now would turn a warning into a guess.
"""

from __future__ import annotations

from collections.abc import Iterable

from .board import BROADCAST
from .grants import Grant

_DERIVED = "derived:"


def recipient_grants(recipient: str, derived, tokens) -> list[Grant]:
    """Every grant the brain holds for clients of ``recipient``."""
    found: list[Grant] = []
    if derived is not None:
        found += [g for g in derived.live_grants() if g.name == f"{_DERIVED}{recipient}"]
    if tokens is not None:
        try:
            entries = tokens.list()
        except ValueError:  # an unreadable token file refuses every token anyway
            entries = []
        for entry in entries:
            # Owner tokens are named for machines, not personas.
            if entry.get("name") == recipient and not entry.get("owner"):
                found.append(Grant(recipient, entry.get("namespaces") or {}))
    return found


def receipt(recipient: str, namespace: str, grants: Iterable[Grant]) -> dict | None:
    """The delivery receipt for one post, or None for a broadcast."""
    if recipient == BROADCAST:
        return None
    grants = list(grants)
    if not grants:
        return {
            "recipient": recipient,
            "visible": None,
            "note": f"stored; visibility unverified - no client of {recipient!r} is known "
            "to this brain",
        }
    if any(g.can_read(namespace) for g in grants):
        return {"recipient": recipient, "visible": True}
    return {
        "recipient": recipient,
        "visible": False,
        "note": f"stored, but no known client of {recipient!r} can read namespace "
        f"{namespace!r} - they will not see it. Post in a namespace they read, or "
        "reply on one of their threads.",
    }
