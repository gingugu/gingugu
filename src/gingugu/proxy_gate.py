"""The big-delete gate in remote mode: checked here, before the brain hears of it.

A big delete (``destructive``) with ``confirm=true`` needs the person's approval,
which gingugu's PreToolUse hook records on THIS machine. The brain on another
host cannot see it, and over HTTP it takes ``confirm=true`` at its word - so the
proxy, which runs here beside the hook, takes the approval before relaying the
call. ``credential_delete`` never reaches the brain: the local vault checks it
itself (``confirm.ask`` under stdio).
"""

from __future__ import annotations

import json

from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, JSONRPCRequest, JSONRPCResponse, TextContent


def screen(msg: JSONRPCRequest) -> SessionMessage | None:
    """A refusal for a big delete the person never approved, or None to relay it.
    Never raises: a gate bug must not end the proxy, and the brain still refuses
    anything malformed."""
    try:
        return _screen(msg)
    except Exception:  # noqa: BLE001
        return None


def _screen(msg: JSONRPCRequest) -> SessionMessage | None:
    if msg.method != "tools/call":
        return None
    params = msg.params or {}
    args = params.get("arguments")
    if not isinstance(args, dict) or args.get("confirm") is not True:
        return None  # omitted or false: the brain asks, or refuses, itself
    from . import confirm, destructive

    tool = destructive.tool_of(str(params.get("name", "")))
    reason = destructive.classify(tool, args)
    if reason is None or confirm.opted_out():
        return None
    from .config import load_config

    if destructive.take(load_config().db_path.parent, tool, args):
        return None
    text = confirm.unapproved(f"{tool}: {reason.split('. ')[0]}")
    body = CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"ok": False, "error": text}, indent=2))]
    ).model_dump(mode="json", by_alias=True, exclude_none=True)
    from .proxy_session import wrap

    return wrap(JSONRPCResponse(jsonrpc="2.0", id=msg.id, result=body))
