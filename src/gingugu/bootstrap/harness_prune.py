"""``gingugu harness --prune``: clear up what ``--migrate`` left, after a restart.

It deletes only what it can prove is ours:

- a stub, only when it is byte-identical to the one migrate wrote and no
  settings file wires it (project, ``settings.local.json``, or user-level);
- a file in ``retired/``, only when the manifest migrate wrote lists it and its
  bytes still match - and never the original behind a stub that is kept.

Directories go only once empty. A ``retired/`` with no manifest is not ours and
is left alone. Symlinks are never followed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .harness_migrate import has_stub_marker, is_untouched_stub
from .harness_retired import (
    LEGACY_SCRIPTS,
    MANIFEST,
    no_symlink_between,
    read_manifest,
    sha256,
)
from .harness_settings import basename, hook_entries, script_tokens
from .settings import user_settings_path


def _settings_commands(target: Path) -> tuple[list[str], bool]:
    """Every hook command in the settings files that apply, and whether all parsed."""
    files = (
        target / ".claude" / "settings.json",
        target / ".claude" / "settings.local.json",
        user_settings_path(),
    )
    commands: list[str] = []
    ok = True
    for path in files:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError, UnicodeDecodeError):
            ok = False
            continue
        if not isinstance(data, dict):
            ok = False
            continue
        commands.extend(str(hook.get("command", "")) for _e, _g, hook in hook_entries(data))
    return commands, ok


def _is_wired(name: str, commands: list[str]) -> bool:
    """Any command naming the script by basename counts - erring toward keeping."""
    return any(name in {basename(t) for t in script_tokens(c)} for c in commands)


def _prune_stubs(hooks: Path, *, dry_run: bool) -> tuple[list[str], set[str]]:
    commands, readable = _settings_commands(hooks.parent.parent)
    lines: list[str] = []
    kept: set[str] = set()
    removed: set[str] = set()
    for path in sorted(hooks.iterdir()):
        if path.is_symlink() or not path.is_file() or not has_stub_marker(path):
            continue
        if not is_untouched_stub(path):
            why = "edited since it was retired"
        elif not readable:
            why = "a settings file does not parse"
        elif _is_wired(path.name, commands):
            why = "still wired"
        else:
            try:
                if not dry_run:
                    path.unlink()
                lines.append(f"  {'would remove' if dry_run else 'removed'} {path}")
                removed.add(path.name)
                continue
            except OSError as exc:
                why = f"could not remove: {exc}"
        kept.add(path.name)
        lines.append(f"  kept    {path}  ({why})")
    # Anything still sitting at a kit script's path - a kept stub, or a file or
    # link the user put there instead - protects the original behind it. A stub
    # this run removes (or, in a dry run, would remove) protects nothing.
    kept |= {p.name for p in hooks.iterdir() if p.name in LEGACY_SCRIPTS} - removed
    return lines, kept


def _remove_empty_dirs(root: Path) -> None:
    for dirpath, _dirs, _files in sorted(os.walk(root), reverse=True):
        path = Path(dirpath)
        try:
            if not path.is_symlink() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass  # left in place; nothing is lost


def _prune_retired(retired: Path, kept_stubs: set[str], *, dry_run: bool) -> list[str]:
    if (retired / MANIFEST).is_symlink():
        return [f"  kept    {retired}  (its manifest is a symlink; not trusted)"]
    manifest = read_manifest(retired)
    if not manifest:
        return [f"  kept    {retired}  (no manifest: not created by --migrate)"]
    lines: list[str] = []
    remaining: dict[str, str] = {}
    removed = 0
    for rel, digest in sorted(manifest.items()):
        path = retired / rel
        if not path.exists() and not path.is_symlink():
            continue
        if rel in kept_stubs:
            why = "its stub is kept"
        elif not no_symlink_between(retired, path):
            why = "inside a symlinked folder; not followed"
        elif path.is_symlink() or not path.is_file() or sha256(path) != digest:
            why = "changed since it was retired"
        else:
            try:
                if not dry_run:
                    path.unlink()
                removed += 1
                continue
            except OSError as exc:
                why = f"could not remove: {exc}"
        remaining[rel] = digest
        lines.append(f"  kept    {path}  ({why})")
    verb = "would remove" if dry_run else "removed"
    lines.append(f"  {verb} {removed} retired file(s) in {retired}")
    if not dry_run:
        if remaining:
            (retired / MANIFEST).write_text(json.dumps(remaining, indent=2, sort_keys=True) + "\n")
        else:
            (retired / MANIFEST).unlink()
        _remove_empty_dirs(retired)
    return lines


def prune(target: Path, *, dry_run: bool) -> list[str]:
    """Delete the stubs and retired files ``--migrate`` left, where provably ours."""
    hooks = target / ".claude" / "hooks"
    if hooks.is_symlink():
        return ["  skip    .claude/hooks is a symlink; nothing pruned"]
    if not hooks.is_dir():
        return ["  nothing to prune - already clean"]
    lines, kept = _prune_stubs(hooks, dry_run=dry_run)
    retired = hooks / "retired"
    if retired.is_symlink():
        lines.append(f"  skip    {retired} is a symlink; not followed")
    elif retired.is_dir():
        lines.extend(_prune_retired(retired, kept, dry_run=dry_run))
    return lines or ["  nothing to prune - already clean"]
