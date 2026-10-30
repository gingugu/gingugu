"""The X-ray half of the PreToolUse hook: what, if anything, to say before a call.

Held apart from ``tool_hook`` to keep that module under its line limit. Three
callers share the one PreToolUse door: the edit tripwire (a deny), the board
nudge (a deny) and the X15 Bash snapshot (silent, never denies).
"""

from __future__ import annotations


def reason(payload: dict, tool_name: str) -> str | None:
    """The X-ray reason for an edit, or None. A bug in it never breaks the hook."""
    try:
        from . import bash_hook, board_nudge, edit_hook

        # Every tool call passes through here: rule out the rest before any I/O.
        nudge = board_nudge.is_post(tool_name, payload.get("tool_input"))
        snap = tool_name == "Bash"  # X15 snapshot: never denies, prints nothing
        if not (nudge or snap) and tool_name not in edit_hook.EDIT_TOOLS:
            return None
        if not nudge and not edit_hook.enabled():
            return None
        from ..config import load_config

        state = load_config().db_path.parent / "hook-sessions"
        if snap:
            bash_hook.before(payload, state)
            return None
        return board_nudge.check(payload, state) if nudge else edit_hook.check(payload, state)
    except Exception:  # noqa: BLE001
        return None
