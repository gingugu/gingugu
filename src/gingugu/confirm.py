"""Destructive tools ask the person, not the model.

``memory_forget(hard_delete=True)``, ``credential_delete`` and a
``memory_import`` that would overwrite memories each take a parameter filled by
an SDK 2.x resolver (``Annotated[..., Resolve(fn)]``) rather than by the model.
The resolver returns ``Elicit`` and the client shows the person a dialog - over
``InputRequiredResult`` on a 2026-07-28 connection, a mid-call
``elicitation/create`` on an older one. The model never sees the parameter: it
is not in the tool's input schema.

Only ``accept`` with ``confirm`` true goes ahead. Decline, cancel (what a
headless client answers), ``confirm`` false, and a client that cannot show a
dialog at all are each a structured refusal - unless the server executing the
tool runs with ``MEMORY_CONFIRM_DESTRUCTIVE=off``, the escape hatch for
automation. Anything this module does not recognise is a refusal: it fails
closed.

A client can declare elicitation and still show nothing: Claude Code's VS Code
extension (2.1.312) answers every request ``decline`` unasked. Taken at its word
that is "the user declined", from someone who never saw a question. It names
itself to the stdio servers it launches, so they - and the proxy, for the brain
behind it - treat it as the client that cannot ask that it is.

Resolvers run before the tool wrapper, so before the grant fence, and a plain
``def`` resolver runs on a worker thread where the sqlite connection is not
usable. So resolvers here are ``async`` and check the fence themselves before
putting a dialog in front of anyone.
"""

from __future__ import annotations

import os
import re
from typing import Any

from mcp.server.elicitation import AcceptedElicitation, CancelledElicitation, DeclinedElicitation
from mcp.server.mcpserver.resolve import Elicit
from mcp.shared.message import SessionMessage
from pydantic import BaseModel, Field

ENV = "MEMORY_CONFIRM_DESTRUCTIVE"

# How a Claude Code client names itself to the servers it launches, and the
# names of those that declare elicitation but show no dialog for it.
ENTRYPOINT = "CLAUDE_CODE_ENTRYPOINT"
_SHOWS_NO_DIALOG = frozenset({"claude-vscode"})

# A resolver's plain (non-``Elicit``) return values. The SDK hands them to the
# tool as ``AcceptedElicitation(data=<value>)``; they are never written to
# ``request_state``, so a client cannot echo one back.
NOT_NEEDED = "gingugu:confirm:not-needed"  # the call is not destructive
OPTED_OUT = "gingugu:confirm:opted-out"  # MEMORY_CONFIRM_DESTRUCTIVE=off
CANNOT_ASK = "gingugu:confirm:cannot-ask"  # the client has no form elicitation
FENCED = "gingugu:confirm:fenced"  # the wrapper refuses before the tool runs


# Control, zero-width and bidi-override characters: none may reach a dialog.
_UNSHOWABLE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def shown(text: Any, limit: int = 80) -> str:
    """A model- or memory-supplied string as it may appear in a dialog: quoted,
    on one line, with no control or bidi characters, and capped - so a steered
    model cannot make "Permanently delete ..." read as something else."""
    flat = " ".join(_UNSHOWABLE.sub(" ", str(text)).split()).replace('"', "'")
    if len(flat) > limit:
        flat = flat[: limit - 1] + "\u2026"
    return f'"{flat}"'


def plain(text: str) -> bool:
    """Whether ``text`` shows as itself: no control, zero-width or bidi character,
    and no backtick to break out of the code span a message puts it in."""
    return "`" not in text and not _UNSHOWABLE.search(text)


class Confirm(BaseModel):
    confirm: bool = Field(description="Tick to confirm. This cannot be undone.")


def fenced(server: Any, tool: str, kwargs: dict | None = None) -> Any:
    """The caller's grant, or None when the tool wrapper will refuse the call -
    in which case the resolver must not show a dialog for it."""
    from .handlers.fence import refusal as fence_refusal
    from .handlers.fence import request_grant

    grant = request_grant(server.transport, server.stdio_grant)
    if grant is None or fence_refusal(tool, grant, kwargs or {}) is not None:
        return None
    return grant


def opted_out() -> bool:
    """True only for an explicit off; any other value keeps confirmations on."""
    return os.environ.get(ENV, "").strip().lower() in ("off", "false", "0", "no")


def shows_no_dialog() -> bool:
    """Whether the client that launched this process answers without asking.

    Meaningful only where that client is this process's own: a stdio server, or
    the proxy. Over HTTP the client is somewhere else."""
    return os.environ.get(ENTRYPOINT, "").strip() in _SHOWS_NO_DIALOG


def withhold_dialogs(item: SessionMessage) -> SessionMessage:
    """The client's ``initialize`` as the proxy forwards it: without elicitation
    when the client shows no dialog, so the brain refuses "cannot ask" instead
    of passing the client's unasked "decline" off as the person's."""
    params = item.message.params or {}
    caps = params.get("capabilities") or {}
    if not shows_no_dialog() or "elicitation" not in caps:
        return item
    kept = {k: v for k, v in caps.items() if k != "elicitation"}
    message = item.message.model_copy(update={"params": {**params, "capabilities": kept}})
    return SessionMessage(message)


def can_ask(ctx: Any, transport: str) -> bool:
    """Whether the client declared form elicitation (a bare ``{}`` counts) and,
    on stdio, is not one known to show no dialog."""
    if transport == "stdio" and shows_no_dialog():
        return False
    try:
        caps = ctx.client_capabilities
    except Exception:  # noqa: BLE001 - no request context: nobody to ask
        return False
    elicitation = caps.elicitation if caps is not None else None
    if elicitation is None:
        return False
    return elicitation.form is not None or elicitation.url is None


def ask(ctx: Any, message: str, transport: str) -> Elicit[Confirm] | str:
    """The resolver's answer for a destructive call: a dialog, or why there is none.
    ``transport`` is the executing server's (``ServerContext.transport``)."""
    if opted_out():
        return OPTED_OUT
    if not can_ask(ctx, transport):
        return CANNOT_ASK
    return Elicit(message, Confirm)


def refusal(answer: Any, what: str, by_hand: str | None = None) -> str | None:
    """Why ``what`` must not happen, or None to go ahead. Call only for a
    destructive call: ``NOT_NEEDED`` here is a refusal, never a pass.
    ``by_hand`` is how a person can do it without a dialog, offered when none
    could be shown."""
    if isinstance(answer, AcceptedElicitation):
        data = answer.data
        if data == OPTED_OUT:
            return None
        if isinstance(data, Confirm):
            return None if data.confirm is True else f"{what} was not confirmed"
        if data == CANNOT_ASK:
            return (
                f"{what} needs the user's confirmation and this client cannot ask "
                f"for it. Use a client that shows MCP elicitation dialogs, or set {ENV}=off "
                "on the server" + (f", or {by_hand}" if by_hand else "")
            )
        return f"{what} was not confirmed"
    if isinstance(answer, DeclinedElicitation):
        return f"the user declined: {what} was not done"
    if isinstance(answer, CancelledElicitation):
        return f"cancelled, or nobody was there to confirm: {what} was not done"
    return f"{what} was not confirmed"
