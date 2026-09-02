"""``gingugu init`` — bootstrap a repo so an AI assistant actually uses the brain.

For **Claude Code** (the default) this installs the real advantage: a
``SessionStart`` hook that auto-injects the memory startup contract every
session, a ``Stop`` hook that enforces save-discipline, and the
``/sink-the-ship`` session-end command. A rules file (the manual approach) is
not guaranteed to be loaded into context; a hook is.

For Windsurf / Cursor / Cline (``--client``) there is no hook system, so we
write the matching rules file with the memory protocol block.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import theme
from ._files import read_template as _read_template
from ._files import safe_read as _safe_read
from .global_rules import init_global_rules, init_repo_rules
from .settings import load_settings, merge_settings, write_settings

CLIENT_RULES_FILES = {
    "windsurf": ".windsurfrules",
    "cursor": ".cursorrules",
    "cline": ".clinerules",
}

# Runtime artifacts the installed hooks (and Claude Code itself) generate. These
# must be git-ignored so a session transcript or local override never lands in
# the repo — especially on a public one.
GITIGNORE_ENTRIES = [
    "logs/",
    ".claude/data/",
    ".claude/settings.local.json",
    ".claude/hooks/**/__pycache__/",
]

_MCP_HINT = (
    "Next steps:\n"
    '  1. Register the Gingugu MCP server in your client under the name "gingugu":\n'
    "       claude mcp add gingugu -- gingugu\n"
    '     (or add it to your client\'s MCP config with the key "gingugu")\n'
    "  2. Restart your client so the SessionStart hook loads."
)


def _ensure_gitignore(target: Path, *, dry_run: bool, results: list[str]) -> None:
    """Append any missing Claude Code / Gingugu ignore rules, non-destructively."""
    path = target / ".gitignore"
    existing = path.read_text() if path.exists() else ""
    present = {line.strip() for line in existing.splitlines()}
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in present]
    if not missing:
        results.append(f"  .gitignore already covers Claude Code artifacts {path}")
        return

    block = "# Claude Code / Gingugu artifacts (added by `gingugu init`)\n"
    block += "\n".join(missing) + "\n"
    if not dry_run:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        prefix = "\n" if existing.strip() else ""
        path.write_text(existing + sep + prefix + block)
    verb = "would update" if dry_run else "updated"
    results.append(f"  {verb} {path}  (+{len(missing)} ignore rule(s))")


# A distinctive marker every file we ship carries. Its absence in a file we are
# about to overwrite means that file is NOT ours, so back it up first.
#
# This was the bare word "gingugu", which is useless as a signature: any hook
# that merely mentions the tool matches, and every gingugu-aware hook does — the
# MCP tool names are `mcp__gingugu__*`. A real, heavily-customized local hook was
# therefore classified as ours and overwritten by `--force` with NO backup, and
# only a clean git tree saved it. The marker has to be something only our
# templates would ever contain.
_TEMPLATE_SIGNATURE = "gingugu-init:managed-file"


def _write_file(
    path: Path,
    content: str,
    *,
    force: bool,
    dry_run: bool,
    results: list[str],
    skip_hint: str = "",
) -> None:
    if path.exists() and not force:
        results.append(f"  skip   {path}  (exists; use --force to overwrite){skip_hint}")
        return

    existing = _safe_read(path) if path.exists() else None
    # Back up whenever `--force` would change what is on disk - NOT only when the
    # file looks foreign.
    #
    # The backup used to be conditioned on _TEMPLATE_SIGNATURE being ABSENT, which
    # meant the net disappeared the moment it did its job: the first `--force`
    # wrote a .bak and stamped the marker, and every `--force` after that saw its
    # own marker and destroyed the user's edits silently. A file being ours says
    # nothing about whether the user has since customized it.
    changed = existing is not None and existing != content
    foreign = (
        existing is not None
        and _TEMPLATE_SIGNATURE in content
        and _TEMPLATE_SIGNATURE not in existing
    )
    if changed and not dry_run:
        (path.parent / f"{path.name}.bak").write_text(existing or "")

    verb = "would write" if dry_run else ("overwrite" if path.exists() else "write")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    results.append(f"  {verb:<9} {path}")

    if foreign:
        results.append(
            f"  WARNING: {path.name} was not written by this version of `gingugu "
            f"init` — it may be your own or another tool's. Backed up to "
            f"{path.name}.bak. If your settings.json invokes it with flags the "
            f"replacement does not declare, that command needs updating too."
        )
    elif changed:
        would = "would back up" if dry_run else "backed up"
        results.append(f"  {would} your version to {path.name}.bak")


def init_claude_code(target: Path, *, force: bool, dry_run: bool, adopt: bool = False) -> list[str]:
    # State the resolved target first. `--path` defaults to the process's cwd,
    # and wrappers move that out from under you — `uv run --directory X` runs in
    # X, so a bare `gingugu init` there bootstraps X, not the directory you typed
    # the command in. Naming the path up front turns a silent wrong-repo write
    # into something you notice on line one.
    results: list[str] = ["Claude Code bootstrap:", f"  target {target}"]
    hooks_dir = target / ".claude" / "hooks"
    commands_dir = target / ".claude" / "commands"

    _write_file(
        hooks_dir / "session_start.py",
        _read_template("session_start.py.tmpl"),
        force=force,
        dry_run=dry_run,
        results=results,
    )
    _write_file(
        hooks_dir / "stop.py",
        _read_template("stop.py.tmpl"),
        force=force,
        dry_run=dry_run,
        results=results,
    )
    _write_file(
        hooks_dir / "user_prompt_recall.py",
        _read_template("user_prompt_recall.py.tmpl"),
        force=force,
        dry_run=dry_run,
        results=results,
    )
    _write_file(
        commands_dir / "sink-the-ship.md",
        _read_template("sink-the-ship.md.tmpl"),
        force=force,
        dry_run=dry_run,
        results=results,
    )

    settings_path = target / ".claude" / "settings.json"
    raw = settings_path.read_text() if settings_path.exists() else None
    settings, added, warnings = merge_settings(load_settings(settings_path), hooks_dir=hooks_dir)
    if added:
        if not dry_run:
            if raw is not None:
                (target / ".claude" / "settings.json.bak").write_text(raw)
            write_settings(settings_path, settings)
        note = " (backed up existing to settings.json.bak)" if raw is not None else ""
        verb = "would wire" if dry_run else "wired"
        results.append(f"  {verb} {', '.join(added)} in {settings_path}{note}")
    elif not warnings:
        results.append(f"  settings.json already wired (no change) {settings_path}")
    else:
        results.append(f"  settings.json left unchanged {settings_path}")
    for warning in warnings:
        results.append(f"  WARNING: {warning}")

    _ensure_gitignore(target, dry_run=dry_run, results=results)

    # The user-level rules file is part of the Claude Code bootstrap, same as the
    # hooks and settings.json — it is what makes the protocol load in sessions
    # where no repo protocol is installed. Non-destructive and idempotent, so it
    # needs no opt-in flag; see global_rules for the merge rules.
    #
    # `force` is deliberately NOT forwarded: it authorizes overwriting the repo
    # files init owns, which is a different and much smaller decision than
    # touching a hand-authored file loaded in every session.
    results.append("")
    results.extend(init_global_rules(dry_run=dry_run, adopt=adopt))

    # Same rationale, aimed at the repo's own CLAUDE.md / AGENTS.md instead of
    # the user-level file. Only touches files that already exist — see
    # init_repo_rules.
    results.append("")
    results.extend(init_repo_rules(target, dry_run=dry_run, adopt=adopt))

    results.append("")
    results.append(_MCP_HINT)
    return results


def init_rules_file(client: str, target: Path, *, force: bool, dry_run: bool) -> list[str]:
    results: list[str] = [f"{client} bootstrap:", f"  target {target}"]
    rules_path = target / CLIENT_RULES_FILES[client]
    protocol = _read_template("rules_protocol.md.tmpl")

    # Routed through _write_file so this path gets the same backup guarantee as
    # the Claude Code hooks. It had none at all: a rules file is hand-authored by
    # the user from line one, and `--force` replaced it with the template
    # outright. Nothing here carries our marker, so the backup rests entirely on
    # the content-changed check.
    _write_file(
        rules_path,
        protocol,
        force=force,
        dry_run=dry_run,
        results=results,
        skip_hint=(". Paste the Memory Protocol section yourself, or re-run with --force."),
    )

    results.append("")
    results.append(
        'Next: register the Gingugu MCP server under the name "gingugu" in your '
        "client's MCP config, then restart it."
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gingugu init",
        description="Bootstrap a repo so an AI assistant uses Gingugu memory.",
    )
    parser.add_argument("--path", default=".", help="Target repo directory (default: current dir)")
    parser.add_argument(
        "--client",
        default="claude-code",
        choices=["claude-code", *CLIENT_RULES_FILES],
        help="Target assistant (default: claude-code)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite files that already exist")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would happen, write nothing"
    )
    parser.add_argument(
        "--adopt",
        action="store_true",
        help=(
            "Wrap an existing hand-written memory protocol (in ~/.claude/CLAUDE.md, "
            "or this repo's CLAUDE.md/AGENTS.md) in gingugu's managed markers, then "
            "refresh it to the template. Without this, a file that already has its "
            "own protocol is left untouched."
        ),
    )
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser().resolve()
    if not target.is_dir():
        print(f"error: target path is not a directory: {target}")
        return 1

    if args.adopt and args.client != "claude-code":
        print("error: --adopt only applies to --client claude-code (the default)")
        return 1

    if args.client == "claude-code":
        results = init_claude_code(target, force=args.force, dry_run=args.dry_run, adopt=args.adopt)
    else:
        results = init_rules_file(args.client, target, force=args.force, dry_run=args.dry_run)

    print(theme.render(results, dry_run=args.dry_run))
    return 0
