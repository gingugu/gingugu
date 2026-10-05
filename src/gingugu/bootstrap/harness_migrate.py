"""``gingugu harness --migrate`` / ``--prune``: moving a repo off an older hook kit.

Migrate unwires the legacy per-event loggers, resets our own hook commands to
their canonical form, and retires the legacy scripts behind exit-0 stubs (a
running Claude Code session keeps calling the hooks it started with, and a
missing script blocks the prompt). Prune, run after a restart, deletes the stubs
and the retired copies. Anything the tool does not know is left exactly as is.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .harness_settings import HARNESS_HOOKS
from .settings import _HOOKS

RETIRED_MARKER = "gingugu-harness:retired"

LEGACY_SCRIPTS = frozenset(
    f"{name}.py"
    for name in (
        "config_change",
        "cwd_changed",
        "elicitation",
        "elicitation_result",
        "file_changed",
        "instructions_loaded",
        "notification",
        "permission_request",
        "post_compact",
        "post_tool_use",
        "post_tool_use_failure",
        "session_end",
        "stop_failure",
        "subagent_start",
        "subagent_stop",
        "task_completed",
        "task_created",
        "teammate_idle",
        "user_prompt_submit",
    )
)

STUB = """#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
# gingugu-harness:retired - unwired by `gingugu harness --migrate`; the original is in retired/.
# A running Claude Code session keeps calling the hooks it started with, so this exits 0.
# Delete it after restarting Claude Code, or run `gingugu harness --prune`.
import sys

sys.exit(0)
"""


def _script_names(command: str) -> set[str]:
    """Basenames of every token in ``command`` that names a ``.py`` script."""
    names = set()
    for token in command.split():
        token = token.strip("\"'")
        if token.endswith(".py"):
            names.add(token.rsplit("/", 1)[-1])
    return names


def _hook_entries(settings: dict):
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


def unwire_legacy(settings: dict) -> list[str]:
    """Remove hook entries running a ``LEGACY_SCRIPTS`` script; returns what went."""
    removed: list[str] = []
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return removed
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        touched = False
        kept_groups = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = []
            for hook in entries:
                legacy = (
                    _script_names(str(hook.get("command", ""))) & LEGACY_SCRIPTS
                    if (isinstance(hook, dict))
                    else set()
                )
                if legacy:
                    removed.extend(f"{event} {name}" for name in sorted(legacy))
                    touched = True
                else:
                    kept.append(hook)
            if kept:
                group["hooks"] = kept
                kept_groups.append(group)
        if touched:
            if kept_groups:
                hooks[event] = kept_groups
            else:
                del hooks[event]
    return removed


def reset_canonical(settings: dict) -> list[str]:
    """Reset our own hooks' command and timeout to canonical; returns what changed.

    Matches on the exact script basename, so ``stop.py`` never captures
    ``subagent_stop.py``.
    """
    changed: list[str] = []
    for event, command, timeout, marker, *_ in [*_HOOKS, *HARNESS_HOOKS]:
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict) or not isinstance(hooks.get(event), list):
            continue
        for ev, _group, hook in _hook_entries({"hooks": {event: hooks[event]}}):
            if marker not in _script_names(str(hook.get("command", ""))):
                continue
            if hook.get("command") != command or hook.get("timeout") != timeout:
                hook["command"], hook["timeout"] = command, timeout
                changed.append(f"{ev} {marker}")
    return changed


def migrate_settings(settings: dict, *, dry_run: bool) -> list[str]:
    """Unwire the legacy loggers and reset our own commands; result lines."""
    unwire, reset = ("would unwire", "would reset") if dry_run else ("unwired", "reset")
    lines = [f"  {unwire} {entry}" for entry in unwire_legacy(settings)]
    lines.extend(
        f"  {reset} {entry} to its canonical command" for entry in reset_canonical(settings)
    )
    return lines


def _has_stub_marker(path: Path) -> bool:
    try:
        return RETIRED_MARKER in path.read_text()
    except (OSError, UnicodeDecodeError):
        return False


def _move(src: Path, dst: Path, *, dry_run: bool) -> bool:
    if dst.exists():
        return False
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return True


def _utils_blockers(hooks: Path) -> list[str]:
    """User-owned hook scripts that mention ``utils`` and may still need it."""
    found = []
    for path in sorted(hooks.glob("*.py")):
        if path.name in LEGACY_SCRIPTS or not path.is_file() or _has_stub_marker(path):
            continue
        try:
            if "utils" in path.read_text():
                found.append(path.name)
        except (OSError, UnicodeDecodeError):
            found.append(path.name)
    return found


def retire_legacy(target: Path, *, dry_run: bool) -> list[str]:
    """Move legacy scripts to ``retired/`` behind stubs, then ``utils/`` after them."""
    hooks = target / ".claude" / "hooks"
    retired = hooks / "retired"
    lines: list[str] = []
    for name in sorted(LEGACY_SCRIPTS):
        path = hooks / name
        if not path.is_file() or _has_stub_marker(path):
            continue
        if not _move(path, retired / name, dry_run=dry_run):
            lines.append(f"  kept    {path}  (retired/{name} already exists; not overwritten)")
            continue
        if not dry_run:
            path.write_text(STUB)
        verb = "would retire" if dry_run else "retired"
        lines.append(f"  {verb} {name} -> retired/{name}, exit-0 stub left in place")
    utils = hooks / "utils"
    if utils.is_dir():
        blockers = _utils_blockers(hooks)
        if blockers:
            lines.append(
                f"  skip    hooks/utils not moved: {', '.join(blockers)} mention it "
                "and may still need it"
            )
        elif not _move(utils, retired / "utils", dry_run=dry_run):
            lines.append("  kept    hooks/utils  (retired/utils already exists; not overwritten)")
        else:
            verb = "would move" if dry_run else "moved"
            lines.append(f"  {verb} hooks/utils -> hooks/retired/utils")
    return lines


def _wired(settings_path: Path) -> tuple[list[str], bool]:
    """Every wired hook command, and whether the file could be read at all."""
    if not settings_path.exists():
        return [], True
    try:
        settings = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        return [], False
    if not isinstance(settings, dict):
        return [], False
    return [str(hook.get("command", "")) for _e, _g, hook in _hook_entries(settings)], True


def _is_wired(name: str, commands: list[str]) -> bool:
    pattern = re.compile(re.escape(f"/.claude/hooks/{name}") + r"""(?=$|[\s"'])""")
    return any(pattern.search(command) for command in commands)


def prune(target: Path, *, dry_run: bool) -> list[str]:
    """Delete retired stubs that nothing wires, and the ``retired/`` directory."""
    hooks = target / ".claude" / "hooks"
    commands, readable = _wired(target / ".claude" / "settings.json")
    lines: list[str] = []
    if hooks.is_dir():
        for path in sorted(p for p in hooks.iterdir() if p.is_file()):
            if not _has_stub_marker(path):
                continue
            if not readable or _is_wired(path.name, commands):
                why = "still wired" if readable else "settings.json does not parse"
                lines.append(f"  kept    {path}  ({why})")
                continue
            if not dry_run:
                path.unlink()
            lines.append(f"  {'would remove' if dry_run else 'removed'} {path}")
    retired = hooks / "retired"
    if retired.is_dir():
        if not dry_run:
            shutil.rmtree(retired)
        lines.append(f"  {'would remove' if dry_run else 'removed'} {retired}")
    if not lines:
        lines.append("  nothing to prune - already clean")
    return lines
