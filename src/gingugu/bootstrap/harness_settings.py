"""Settings wiring for `gingugu harness`: hooks and deny rules, merged additively."""

from __future__ import annotations

import re
from pathlib import Path

from .settings import load_settings, merge_settings, write_settings

_RUN = "uv run $CLAUDE_PROJECT_DIR/.claude/hooks"
LOG_EVENT_CMD = f"{_RUN}/log_event.py"

# Events log_event.py records. FileChanged only fires for the matched names;
# CwdChanged takes no matcher at all.
_LOGGED_EVENTS = (
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "SessionEnd",
    "PostCompact",
    "PermissionRequest",
    "StopFailure",
    "TaskCreated",
    "TaskCompleted",
    "TeammateIdle",
    "ConfigChange",
    "CwdChanged",
    "FileChanged",
    "InstructionsLoaded",
    "Elicitation",
    "ElicitationResult",
)
_MATCHERS: dict[str, str | None] = {
    "FileChanged": ".env|.envrc|CLAUDE.md",
    "CwdChanged": None,
}

HARNESS_HOOKS: list[tuple] = [
    ("PreToolUse", f"{_RUN}/pre_tool_use.py", 10, "pre_tool_use.py"),
    ("PreCompact", f"{_RUN}/pre_compact.py --backup", 15, "pre_compact.py"),
    *[
        (event, LOG_EVENT_CMD, 10, "log_event.py", _MATCHERS.get(event, ""))
        for event in _LOGGED_EVENTS
    ],
]

HARNESS_DENY = (
    "Agent(model:opus)",
    "Bash(git push --force:*)",
    "Bash(git push -f:*)",
    "Bash(git reset --hard:*)",
    "Bash(rm -rf:*)",
)


def merge_deny(settings: dict) -> list[str]:
    """Add any missing ``HARNESS_DENY`` rule to ``permissions.deny``; returns those added.

    Never removes an entry. An unexpected shape is left alone.
    """
    permissions = settings.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        return []
    deny = permissions.setdefault("deny", [])
    if not isinstance(deny, list):
        return []
    added = [rule for rule in HARNESS_DENY if rule not in deny]
    deny.extend(added)
    return added


def wire_harness_settings(
    target: Path, *, dry_run: bool, user_original_kept: bool
) -> tuple[list[str], list[str]]:
    """Merge the harness hooks and deny rules into ``<target>/.claude/settings.json``.

    ``user_original_kept`` says there is nothing to back up: either no settings
    file existed, or init already saved the user's original to
    ``settings.json.bak`` this run and it must not be overwritten.
    Returns (result lines, warnings).
    """
    path = target / ".claude" / "settings.json"
    raw = path.read_text() if path.exists() else None
    settings, added, warnings = merge_settings(
        load_settings(path), hooks_dir=target / ".claude" / "hooks", hooks=HARNESS_HOOKS
    )
    denied = merge_deny(settings)
    if denied:
        added = [*added, *(f"permissions.deny {rule}" for rule in denied)]
    lines: list[str] = []
    if added:
        backed_up = False
        if not dry_run:
            if raw is not None and not user_original_kept:
                (path.parent / "settings.json.bak").write_text(raw)
                backed_up = True
            write_settings(path, settings)
        verb = "would wire" if dry_run else "wired"
        note = " (backed up existing to settings.json.bak)" if backed_up else ""
        lines.append(f"  {verb} {len(added)} harness entries in {path}{note}")
    else:
        lines.append(f"  settings.json harness entries already wired (no change) {path}")
    lines.extend(f"  WARNING: {w}" for w in warnings)
    return lines, warnings


_TOKEN_SPLIT = re.compile(r"[\s;|&<>()]+")


def script_tokens(command: str) -> list[str]:
    """Every token in a shell command that names a ``.py`` file, quotes dropped."""
    raw = (
        t.replace('"', "").replace("'", "").replace("\\", "/") for t in _TOKEN_SPLIT.split(command)
    )
    return [t for t in raw if t.endswith(".py")]


def basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


_PROJECT_HOOK_PREFIXES = (
    "$CLAUDE_PROJECT_DIR/.claude/hooks/",
    "${CLAUDE_PROJECT_DIR}/.claude/hooks/",
    ".claude/hooks/",
    "./.claude/hooks/",
)


def project_hook(token: str, hooks_dir: Path) -> str | None:
    """The script name if ``token`` is a file directly in THIS project's hooks dir.

    ``~/.claude/hooks/x.py`` (user-level) and ``/opt/team/x.py`` are not.
    """
    for prefix in _PROJECT_HOOK_PREFIXES:
        if token.startswith(prefix) and "/" not in token[len(prefix) :]:
            return token[len(prefix) :]
    path = Path(token)
    if path.is_absolute() and path.parent == hooks_dir:
        return path.name
    return None


def hook_entries(settings: dict):
    """Yield (event, group, hook) for every well-formed hook entry."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for hook in group["hooks"]:
                if isinstance(hook, dict):
                    yield event, group, hook
