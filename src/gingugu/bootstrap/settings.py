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


# Flags our own hook scripts accept. A command wired to one of our script
# names but carrying anything else was written for a DIFFERENT script.
_KNOWN_FLAGS = {
    "session_start.py": set(),
    "stop.py": {"--check-memory-saves", "--min-tool-calls"},
}


def _matching_commands(groups: list, marker: str) -> list[str]:
    """Every wired command containing ``marker``."""
    found: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []):
            if isinstance(hook, dict) and marker in str(hook.get("command", "")):
                found.append(str(hook.get("command", "")))
    return found


def _has_command(groups: list, marker: str) -> bool:
    """True if any hook group already wires a command containing ``marker``."""
    return bool(_matching_commands(groups, marker))


def foreign_flags(command: str, marker: str) -> list[str]:
    """Flags in ``command`` that our ``marker`` script does not accept.

    Matching on a bare filename is not enough to call a hook "already wired".
    A repo bootstrapped by other tooling may point at a same-named script with
    its own flags; installing ours over it produces a command whose flags the
    new script has never heard of. Reporting that beats claiming success.
    """
    known = _KNOWN_FLAGS.get(marker, set())
    return [
        token
        for token in command.split()
        if token.startswith("--") and token.split("=")[0] not in known
    ]


def _hook_group(command: str, timeout: int) -> dict:
    return {
        "matcher": "",
        "hooks": [{"type": "command", "command": command, "timeout": timeout}],
    }


def merge_settings(settings: dict) -> tuple[dict, list[str], list[str]]:
    """Return (updated settings, events added, warnings about existing wiring).

    Mutates a copy-friendly nested structure; caller owns persistence.

    A warning is emitted when an event is skipped because a command already
    mentions our script name, but that command carries flags our script does
    not accept. That is the case where "already wired" is actively misleading:
    the wiring belongs to some other tool's script of the same name.
    """
    added: list[str] = []
    warnings: list[str] = []
    hooks = settings.setdefault("hooks", {})
    for event, command, timeout, marker in _HOOKS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            continue  # respect an unexpected shape rather than clobber it
        existing = _matching_commands(groups, marker)
        if existing:
            for wired in existing:
                unknown = foreign_flags(wired, marker)
                if unknown:
                    warnings.append(
                        f"{event}: existing command carries flag(s) "
                        f"{' '.join(unknown)} that gingugu's {marker} does not "
                        f"accept — it was written for a different script. "
                        f"Left as-is; reconcile it by hand."
                    )
            continue
        groups.append(_hook_group(command, timeout))
        added.append(event)
    return settings, added, warnings


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
