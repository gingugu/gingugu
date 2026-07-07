"""Non-destructive merge of Gingugu hooks into a repo's .claude/settings.json.

The target repo may already have a settings.json with its own hooks and
permissions. We add only our SessionStart + Stop entries, back up any existing
file first, and never touch anything else. Idempotent: re-running is a no-op.
"""

from __future__ import annotations

import json
from pathlib import Path

SESSION_START_CMD = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks/session_start.py"
STOP_CMD = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks/stop.py --check-memory-saves"

# (event name, command, timeout, marker used to detect an existing entry)
_HOOKS = [
    ("SessionStart", SESSION_START_CMD, 15, "session_start.py"),
    ("Stop", STOP_CMD, 30, "stop.py"),
]


def _has_command(groups: list, marker: str) -> bool:
    """True if any hook group already wires a command containing ``marker``."""
    for group in groups:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []):
            if isinstance(hook, dict) and marker in str(hook.get("command", "")):
                return True
    return False


def _hook_group(command: str, timeout: int) -> dict:
    return {
        "matcher": "",
        "hooks": [{"type": "command", "command": command, "timeout": timeout}],
    }


def merge_settings(settings: dict) -> tuple[dict, list[str]]:
    """Return (updated settings, list of events that were added).

    Mutates a copy-friendly nested structure; caller owns persistence.
    """
    added: list[str] = []
    hooks = settings.setdefault("hooks", {})
    for event, command, timeout, marker in _HOOKS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            continue  # respect an unexpected shape rather than clobber it
        if _has_command(groups, marker):
            continue
        groups.append(_hook_group(command, timeout))
        added.append(event)
    return settings, added


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_settings(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
