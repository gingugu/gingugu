"""PostToolUse hook: the X-ray map and a read file's neighbourhood.

Invoked as ``gingugu hook read`` for Read, Grep and Glob. The text rides
``additionalContext`` next to the tool result; it informs and never blocks.
"""

from __future__ import annotations

import json
import sys

_READ_TOOLS = ("Read", "Grep", "Glob")


def run(payload: dict) -> int:
    """Decide and print. Returns the process exit code (always 0)."""
    try:
        tool = payload.get("tool_name")
        if tool not in _READ_TOOLS and tool != "Bash":
            return 0
        from .config import load_config
        from .xray import bash_hook, read_hook

        state = load_config().db_path.parent / "hook-sessions"
        if tool == "Bash":
            text = bash_hook.after(payload, state)  # gated by the edit switch inside
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
