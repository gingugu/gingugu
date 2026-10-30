"""PostToolUse hook: the X-ray map, a read file's neighbourhood, the note after an edit.

Invoked as the ``read`` hook for Read, Grep and Glob, ``bash`` for Bash (changes
a command made, and the test ledger) and ``edit`` for Edit, Write and MultiEdit. The text rides
``additionalContext`` next to the tool result; it informs and never blocks.
"""

from __future__ import annotations

import json
import sys

_READ_TOOLS = ("Read", "Grep", "Glob")
_EDIT_TOOLS = ("Edit", "Write", "MultiEdit")


def run(payload: dict) -> int:
    """Decide and print. Returns the process exit code (always 0)."""
    try:
        tool = payload.get("tool_name")
        if tool not in _READ_TOOLS and tool not in _EDIT_TOOLS and tool != "Bash":
            return 0
        from .config import load_config
        from .xray import bash_hook, edit_note, ledger, read_hook

        state = load_config().db_path.parent / "hook-sessions"
        if tool == "Bash":
            ledger.observe(payload, state)  # records test runs; never raises
            text = bash_hook.after(payload, state)  # gated by the edit switch inside
        elif tool in _EDIT_TOOLS:
            text = edit_note.check(payload, state)  # gated by the edit switch inside
        elif read_hook.enabled():
            text = read_hook.check(payload, state)
        else:
            return 0
        if text:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PostToolUse",
                            "additionalContext": text,
                        }
                    }
                )
            )
    except Exception:  # noqa: BLE001 - a bug in the map never breaks a read
        pass
    return 0


def main() -> int:
    """Never raises. A hook that crashes on a tool call is worse than no hook."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        return run(payload)
    except Exception:  # noqa: BLE001
        return 0
