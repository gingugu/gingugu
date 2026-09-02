"""Non-destructive merge of Gingugu hooks into a repo's .claude/settings.json.

The target repo may already have a settings.json with its own hooks and
permissions. We add only our SessionStart + Stop entries, back up any existing
file first, and never touch anything else. Idempotent: re-running is a no-op.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SESSION_START_CMD = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks/session_start.py"
STOP_CMD = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks/stop.py --check-memory-saves"
PROMPT_RECALL_CMD = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks/user_prompt_recall.py"

# (event name, command, timeout, marker used to detect an existing entry)
#
# UserPromptSubmit gets 20s rather than the event's 30s default: the hook is
# sequential and the user is waiting on it, so a hung encoder should surrender
# the turn well before Claude Code would give up on it.
_HOOKS = [
    ("SessionStart", SESSION_START_CMD, 15, "session_start.py"),
    ("Stop", STOP_CMD, 30, "stop.py"),
    ("UserPromptSubmit", PROMPT_RECALL_CMD, 20, "user_prompt_recall.py"),
]


# Fallback only: the flags OUR shipped hook scripts accept, used when the script
# actually installed on disk cannot be read. Prefer ``declared_flags`` — a
# hardcoded list can only describe our own template, and a repo may legitimately
# run a richer same-named script that accepts more.
_KNOWN_FLAGS = {
    "session_start.py": set(),
    "stop.py": {"--check-memory-saves", "--min-tool-calls"},
    "user_prompt_recall.py": set(),
}

# `parser.add_argument("--flag"` / `'--flag'`, across line breaks.
_ADD_ARGUMENT_FLAG = re.compile(r"""add_argument\(\s*["'](--[\w-]+)["']""")


def declared_flags(script: Path) -> set[str] | None:
    """Long flags the script at ``script`` actually declares, or None if unreadable.

    Read from the file rather than assumed, because the question that matters is
    whether the script that will RUN accepts the wired flags — not whether our
    template does. A repo's own hook may be a superset of ours (this repo's
    ``stop.py`` accepts ``--chat`` and ``--notify``), and calling that "written
    for a different script" is a false alarm.
    """
    try:
        source = script.read_text()
    except OSError:
        return None
    return set(_ADD_ARGUMENT_FLAG.findall(source))


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


def foreign_flags(command: str, marker: str, *, script: Path | None = None) -> list[str]:
    """Flags in ``command`` that the ``marker`` script on disk does not accept.

    Matching on a bare filename is not enough to call a hook "already wired".
    A repo bootstrapped by other tooling may point at a same-named script with
    its own flags; installing ours over it produces a command whose flags the
    new script has never heard of. Reporting that beats claiming success.

    ``script`` is the installed hook. When given and readable, its own
    ``add_argument`` declarations decide the answer, so a repo running a richer
    same-named hook is not flagged. Falls back to ``_KNOWN_FLAGS`` otherwise.
    """
    known = declared_flags(script) if script is not None else None
    if known is None:
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


def merge_settings(
    settings: dict, *, hooks_dir: Path | None = None
) -> tuple[dict, list[str], list[str]]:
    """Return (updated settings, events added, warnings about existing wiring).

    Mutates a copy-friendly nested structure; caller owns persistence.

    A warning is emitted when an event is skipped because a command already
    mentions our script name, but that command carries flags the script ON DISK
    does not accept. That is the case where "already wired" is actively
    misleading: the flags are orphaned and will be silently ignored at runtime.

    ``hooks_dir`` is the installed ``.claude/hooks`` directory. Pass it so the
    check reads the real script instead of assuming our template's flag set;
    without it the check falls back to that assumption and can cry wolf over a
    repo running a legitimately richer hook of the same name.
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
            script = (hooks_dir / marker) if hooks_dir is not None else None
            for wired in existing:
                unknown = foreign_flags(wired, marker, script=script)
                if unknown:
                    warnings.append(
                        f"{event}: existing command carries flag(s) "
                        f"{' '.join(unknown)} that the installed {marker} does "
                        f"not declare, so they will be silently ignored at "
                        f"runtime. Left as-is; reconcile it by hand."
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
