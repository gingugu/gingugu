"""The big-delete gate: deletes big or unrecoverable enough that the person decides.

An ordinary delete runs on the caller's ``confirm=true`` (see ``confirm``). These
do not: a namespace delete, a credential delete (a keychain secret has no export
and no undo), a consolidation or replace-import over ``THRESHOLD`` memories, and
the hard deletes past ``THRESHOLD`` in one session.

gingugu's PreToolUse hook (``hook_check``) answers ``ask`` for them, so Claude
Code shows its own permission prompt. That holds in auto and bypass mode and
becomes a deny where nobody can answer (measured 2026-11-05, ``claude -p``).
The hook also leaves a single-use approval for exactly that call; a local server
(``confirm.ask``) and the proxy in remote mode (``proxy_gate.screen``)
require it before acting on ``confirm=true``. A client with no gingugu hook
gets a dialog instead, or a refusal. Per-session hard-delete counting is the
hook's alone: one tool call cannot see its siblings.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import NamedTuple

from .confirm import shown

THRESHOLD = 10
TTL = 15 * 60  # the person may take a while to answer the prompt
_DIR = "approvals"
_FORGETS = "hard-deletes-{}.json"


def tool_of(name: str) -> str:
    """``mcp__<server>__<tool>`` -> ``<tool>``; anything else unchanged."""
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name


def _ids(args: dict) -> list[str]:
    return sorted(m.strip() for m in str(args.get("memory_ids") or "").split(",") if m.strip())


def _memories(args: dict) -> list:
    data = args.get("data")
    memories = data.get("memories") if isinstance(data, dict) else None
    return memories if isinstance(memories, list) else []


def classify(tool: str, args: dict) -> str | None:
    """What this call would destroy, if it is a big delete; else None."""
    if tool == "memory_namespaces" and args.get("action") == "delete":
        what = " and every memory in it" if args.get("cascade") else ""
        return f"delete namespace {shown(args.get('name'))}{what}. This cannot be undone."
    if tool == "credential_delete":
        field = args.get("field_name")
        target = shown(args.get("service_name"))
        target = f"field {shown(field)} of {target}" if field else target
        return f"delete credential {target} from the keychain. This cannot be undone."
    if tool == "memory_consolidate" and args.get("keep_originals") is False:
        n = len(_ids(args))
        if n > THRESHOLD:
            return f"consolidate {n} memories and delete the originals. This cannot be undone."
    if tool == "memory_import" and args.get("on_conflict") == "replace":
        n = len(_memories(args))
        if n > THRESHOLD:
            return f"import {n} memories over any with the same id. This cannot be undone."
    return None


class Gate(NamedTuple):
    data_dir: Path
    tool: str
    args: dict
    owner: bool  # the full grant: over HTTP, the owner's proxy took the approval


def gate(server: object, tool: str, args: dict) -> Gate | None:
    """What a resolver hands ``confirm.ask``: set only for a big delete.
    ``server`` is the ``ServerContext``; the proxy's local vault builds one
    without a config, so the data dir then comes from this process's own."""
    if classify(tool, args) is None:
        return None
    config = getattr(server, "config", None)
    if config is None:
        from .config import load_config

        config = load_config()
    from .grants import FULL
    from .handlers import fence

    grant = fence.request_grant(
        getattr(server, "transport", "unknown"), getattr(server, "stdio_grant", FULL)
    )
    return Gate(config.db_path.parent, tool, args, grant is not None and grant.is_full)


def _target(tool: str, args: dict) -> object:
    """What the approval is for: the identifying arguments only, so the server's
    filled-in defaults match what the model sent the hook."""
    if tool == "memory_namespaces":
        return [args.get("name"), bool(args.get("cascade"))]
    if tool == "credential_delete":
        return [args.get("service_name"), args.get("field_name")]
    if tool == "memory_consolidate":
        return _ids(args)
    if tool == "memory_import":
        return hashlib.sha256(json.dumps(args.get("data"), sort_keys=True).encode()).hexdigest()
    return None


def _path(data_dir: Path, tool: str, args: dict) -> Path:
    body = json.dumps([tool, _target(tool, args)], sort_keys=True, default=str)
    return Path(data_dir) / "hook-sessions" / _DIR / hashlib.sha256(body.encode()).hexdigest()


def approve(data_dir: Path, tool: str, args: dict, now: float | None = None) -> None:
    """Record that the person is being asked about this exact call."""
    path = _path(data_dir, tool, args)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str((time.time() if now is None else now) + TTL))
    os.chmod(path, 0o600)


def take(data_dir: Path, tool: str, args: dict, now: float | None = None) -> bool:
    """Use up the approval for this call. True only once, and only before it expires."""
    path = _path(data_dir, tool, args)
    try:
        expires = float(path.read_text())
        path.unlink()  # whoever unlinks it owns it: two callers cannot both pass
    except (OSError, ValueError):
        return False
    return (time.time() if now is None else now) <= expires


def _bump_forgets(data_dir: Path, session_id: str) -> int:
    from .subagent_hook import safe_session

    path = Path(data_dir) / "hook-sessions" / _FORGETS.format(safe_session(session_id))
    try:
        count = int(json.loads(path.read_text()).get("count", 0)) + 1
    except (OSError, ValueError, AttributeError):
        count = 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"count": count}))
    except OSError:
        pass
    return count


def hook_check(payload: dict, data_dir: Path) -> str | None:
    """The PreToolUse side: why the person must be asked about this call, or None.
    Records the approval it asks about. Never raises."""
    tool_name = str(payload.get("tool_name") or "")
    args = payload.get("tool_input")
    if not tool_name.startswith("mcp__") or not isinstance(args, dict):
        return None
    tool = tool_of(tool_name)
    try:
        reason = classify(tool, args)
        if reason is None and tool == "memory_forget" and args.get("hard_delete") is True:
            count = _bump_forgets(data_dir, str(payload.get("session_id") or "unknown"))
            if count > THRESHOLD:
                reason = (
                    f"permanently delete memory {shown(args.get('memory_id'))}, hard delete "
                    f"number {count} this session. This cannot be undone."
                )
    except Exception:  # noqa: BLE001 - a malformed call is the server's to refuse
        return None
    if reason is None:
        return None
    try:
        approve(data_dir, tool, args)
    except OSError:
        pass  # unrecorded: the server refuses later instead of acting unasked
    return f"gingugu: {reason}"


def hook_output(payload: dict, data_dir: Path) -> dict | None:
    """The PreToolUse answer for a big delete: Claude Code's own prompt."""
    reason = hook_check(payload, data_dir)
    if reason is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }
