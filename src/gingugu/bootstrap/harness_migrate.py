"""``gingugu harness --migrate``: moving a repo off an older per-event hook kit.

Migrate unwires the legacy loggers, resets our own hook commands to their
canonical form, and retires the legacy scripts behind exit-0 stubs (a running
Claude Code session keeps calling the hooks it started with, and a missing
script blocks the prompt). ``harness_prune`` clears up after a restart.

Only what is provably the kit's is touched. A file NAME proves nothing -
``notification.py`` or ``permission_request.py`` may be the user's own - so a
kit script must also carry the kit's fingerprint (it names its own JSON-array
log, ``<name>.json``), and only the kit's known ``utils/`` files move. Every
file moved into ``retired/`` is hashed into a manifest, which is the only thing
prune will delete by. Symlinks are never written through or followed.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .harness_retired import (
    KIT_UTILS,
    LEGACY_SCRIPTS,
    MANIFEST,
    no_symlink_between,
    read_manifest,
    sha256,
)
from .harness_settings import HARNESS_HOOKS, hook_entries, project_hook, script_tokens
from .settings import _HOOKS

RETIRED_MARKER = "gingugu-harness:retired"

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


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def has_stub_marker(path: Path) -> bool:
    return RETIRED_MARKER in (_read_text(path) or "")


def is_untouched_stub(path: Path) -> bool:
    return not path.is_symlink() and _read_text(path) == STUB


def is_kit_script(path: Path) -> bool:
    """A legacy-named script that also carries the kit's fingerprint."""
    if path.name not in LEGACY_SCRIPTS or path.is_symlink() or not path.is_file():
        return False
    text = _read_text(path) or ""
    return f"{path.stem}.json" in text and "logs" in text


def _retirable(hooks_dir: Path, name: str) -> bool:
    """A legacy entry may be unwired: it runs the kit's script, our stub, or nothing."""
    path = hooks_dir / name
    if path.is_symlink():
        return False
    return not path.exists() or is_kit_script(path) or is_untouched_stub(path)


def _legacy_in(command: str, hooks_dir: Path) -> set[str]:
    """The kit scripts a command runs - but only if that is ALL it runs.

    Every script must be this project's own hook, legacy-named and retirable; a
    command that also runs anything else (the user's ``mine.py``, a user-level
    ``~/.claude/hooks/`` script) is kept whole.
    """
    names = [project_hook(t, hooks_dir) for t in script_tokens(command)]
    if not names or any(n not in LEGACY_SCRIPTS or not _retirable(hooks_dir, n) for n in names):
        return set()
    return set(names)


def unwire_legacy(settings: dict, hooks_dir: Path) -> list[str]:
    """Remove hook entries running the kit's scripts; returns what went."""
    removed: list[str] = []
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return removed
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        touched, kept_groups = False, []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = []
            for hook in entries:
                legacy = (
                    _legacy_in(str(hook.get("command", "")), hooks_dir)
                    if isinstance(hook, dict)
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


def reset_canonical(settings: dict, hooks_dir: Path) -> list[str]:
    """Reset our own hooks' command and timeout to canonical.

    Only an entry running this project's own copy of the script, by exact name -
    a ``~/tools/stop.py`` of the user's is not ours.
    """
    changed: list[str] = []
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return changed
    for event, command, timeout, marker, *_ in [*_HOOKS, *HARNESS_HOOKS]:
        if not isinstance(hooks.get(event), list):
            continue
        for ev, _group, hook in hook_entries({"hooks": {event: hooks[event]}}):
            tokens = script_tokens(str(hook.get("command", "")))
            if marker not in {project_hook(t, hooks_dir) for t in tokens}:
                continue
            if hook.get("command") != command or hook.get("timeout") != timeout:
                hook["command"], hook["timeout"] = command, timeout
                changed.append(f"{ev} {marker}")
    return changed


def migrate_settings(settings: dict, hooks_dir: Path, *, dry_run: bool) -> list[str]:
    """Unwire the kit's loggers and reset our own commands; result lines."""
    unwire, reset = ("would unwire", "would reset") if dry_run else ("unwired", "reset")
    lines = [f"  {unwire} {entry}" for entry in unwire_legacy(settings, hooks_dir)]
    lines.extend(
        f"  {reset} {entry} to its canonical command"
        for entry in reset_canonical(settings, hooks_dir)
    )
    return lines


def _retire(src: Path, retired: Path, rel: str, manifest: dict, dry_run: bool) -> bool:
    dest = retired / rel
    if dest.exists() or dest.is_symlink() or not no_symlink_between(retired, dest):
        return False
    if not dry_run:
        digest = sha256(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        manifest[rel] = digest
    return True


def _utils_blockers(hooks: Path) -> list[str]:
    """User-owned hook scripts that mention ``utils`` and may still need it."""
    found = []
    for path in sorted(hooks.glob("*.py")):
        if not path.is_file() or is_kit_script(path) or has_stub_marker(path):
            continue
        if "utils" in (_read_text(path) or "utils"):
            found.append(path.name)
    return found


def _retire_utils(hooks: Path, retired: Path, manifest: dict, dry_run: bool) -> list[str]:
    utils = hooks / "utils"
    if not utils.is_dir() or utils.is_symlink():
        return []
    blockers = _utils_blockers(hooks)
    if blockers:
        return [f"  skip    hooks/utils: {', '.join(blockers)} mention it and may still need it"]
    moved = 0
    for rel in KIT_UTILS:
        src = utils / rel
        if src.is_file() and not src.is_symlink():
            moved += _retire(src, retired, f"utils/{rel}", manifest, dry_run)
    if not dry_run:
        for dirpath, _dirs, _files in sorted(os.walk(utils), reverse=True):
            path = Path(dirpath)
            try:
                if not path.is_symlink() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass  # left in place; nothing is lost
    if not moved:
        return []
    verb = "would move" if dry_run else "moved"
    return [f"  {verb} {moved} kit file(s) from hooks/utils to hooks/retired/utils"]


def retire_legacy(target: Path, *, dry_run: bool) -> list[str]:
    """Move the kit's scripts to ``retired/`` behind stubs, then its ``utils/`` files."""
    hooks = target / ".claude" / "hooks"
    retired = hooks / "retired"
    if hooks.is_symlink() or retired.is_symlink() or (retired / MANIFEST).is_symlink():
        return ["  skip    retiring: a symlink in .claude/hooks/retired; nothing moved"]
    manifest = read_manifest(retired)
    before = dict(manifest)
    lines: list[str] = []
    for name in sorted(LEGACY_SCRIPTS):
        path = hooks / name
        if not is_kit_script(path):
            if path.exists() and not has_stub_marker(path):
                lines.append(f"  kept    {path}  (not recognised as the old kit's)")
            continue
        if not _retire(path, retired, name, manifest, dry_run):
            lines.append(f"  kept    {path}  (retired/{name} already exists; not overwritten)")
            continue
        if not dry_run:
            path.write_text(STUB)
        verb = "would retire" if dry_run else "retired"
        lines.append(f"  {verb} {name} -> retired/{name}, exit-0 stub left in place")
    lines.extend(_retire_utils(hooks, retired, manifest, dry_run))
    if manifest != before and not dry_run:
        (retired / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return lines
