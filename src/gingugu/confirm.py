"""Destructive tools need a confirmation: the caller's, or the person's in a dialog.

``memory_forget(hard_delete=True)``, ``credential_delete``, a cascading
``memory_namespaces`` delete, ``memory_consolidate(keep_originals=False)`` and a
``memory_import`` that would overwrite memories each take a ``confirm`` argument
and a parameter filled by an SDK 2.x resolver (``Annotated[..., Resolve(fn)]``)
rather than by the model.

``confirm=true`` is the confirmation: the agent asks the user in chat and the
harness permission prompt gates the call, so no daily delete dead-ends on a
client with no dialog. ``confirm=false`` refuses. Omitted, the resolver returns
``Elicit`` and the client shows the person a dialog - over
``InputRequiredResult`` on a 2026-07-28 connection, a mid-call
``elicitation/create`` on an older one. The dialog's parameter is not in the
tool's input schema.

From a dialog, only ``accept`` with ``confirm`` true goes ahead. Decline, cancel
(what a headless client answers) and ``confirm`` false refuse; a client that
cannot show a dialog is told to ask the user and call again with
``confirm=true``. ``MEMORY_CONFIRM_DESTRUCTIVE=off`` on the executing server
skips the dialog for automation. Anything this module does not recognise is a
refusal: it fails closed.

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
from typing import Any

from mcp.server.elicitation import AcceptedElicitation, CancelledElicitation, DeclinedElicitation
from mcp.server.mcpserver.resolve import Elicit
from mcp.shared.message import SessionMessage
from pydantic import BaseModel, Field

from .showable import clean

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
BY_CALLER = "gingugu:confirm:by-caller"  # the caller passed confirm=true
CALLER_SAID_NO = "gingugu:confirm:caller-said-no"  # the caller passed confirm=false
UNAPPROVED = "gingugu:confirm:unapproved"  # a big delete the person never approved


def shown(text: Any, limit: int = 80) -> str:
    """A model- or memory-supplied string as it may appear in a dialog: quoted,
    on one line, with no control or bidi characters, and capped - so a steered
    model cannot make "Permanently delete ..." read as something else."""
    flat = " ".join(clean(text).split()).replace('"', "'")
    if len(flat) > limit:
        flat = flat[: limit - 1] + "\u2026"
    return f'"{flat}"'


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


def _big(ctx: Any, message: str, transport: str, gate: Any) -> Elicit[Confirm] | str:
    """``confirm=true`` on a big delete (``destructive.Gate``): the hook's approval
    for this call, a dialog, or nothing. Over HTTP this machine's approvals are
    not the caller's: the owner's proxy takes them before relaying, so only the
    owner's word stands there; a scoped token gets the person or nothing."""
    if gate is None:
        return BY_CALLER
    if opted_out():
        return OPTED_OUT
    if transport != "stdio":
        if gate.owner:
            return BY_CALLER
    else:
        from .destructive import take

        if take(gate.data_dir, gate.tool, gate.args):
            return BY_CALLER
    return Elicit(message, Confirm) if can_ask(ctx, transport) else UNAPPROVED


def ask(
    ctx: Any,
    message: str,
    transport: str,
    confirmed: bool | None = None,
    gate: tuple | None = None,
) -> Elicit[Confirm] | str:
    """The resolver's answer for a destructive call: a dialog, or why there is none.
    ``transport`` is the executing server's (``ServerContext.transport``).
    ``confirmed`` is the caller's ``confirm`` argument: true is the confirmation
    itself, false refuses, and only None leaves it to a dialog. ``gate`` is
    ``destructive.gate(...)``: set, ``confirm=true`` also needs the person."""
    if confirmed is True:
        return _big(ctx, message, transport, gate)
    if confirmed is not None:
        return CALLER_SAID_NO
    if opted_out():
        return OPTED_OUT
    if not can_ask(ctx, transport):
        return CANNOT_ASK
    return Elicit(message, Confirm)


def unapproved(what: str) -> str:
    """Why a big delete (``destructive``) with ``confirm=true`` alone was refused."""
    return (
        f"{what} is a big delete and needs the user's own approval, which this call "
        "does not have. In Claude Code gingugu's hook asks for it in the permission "
        "prompt; if no prompt appeared, the hook is not installed for this project"
    )


def refusal(answer: Any, what: str) -> str | None:
    """Why ``what`` must not happen, or None to go ahead. Call only for a
    destructive call: ``NOT_NEEDED`` here is a refusal, never a pass."""
    if isinstance(answer, AcceptedElicitation):
        data = answer.data
        if data in (OPTED_OUT, BY_CALLER):
            return None
        if isinstance(data, Confirm):
            return None if data.confirm is True else f"{what} was not confirmed"
        if data == CALLER_SAID_NO:
            return f"{what} was not done: confirm is false"
        if data == UNAPPROVED:
            return unapproved(what)
        if data == CANNOT_ASK:
            return (
                f"{what} needs the user's confirmation and this client cannot ask for "
                "it in a dialog. Ask the user, and once they agree call again with "
                "confirm=true"
            )
        return f"{what} was not confirmed"
    if isinstance(answer, DeclinedElicitation):
        return f"the user declined: {what} was not done"
    if isinstance(answer, CancelledElicitation):
        return f"cancelled, or nobody was there to confirm: {what} was not done"
    return f"{what} was not confirmed"
