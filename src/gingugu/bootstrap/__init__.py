"""``gingugu init`` — bootstrap a repo so an AI assistant actually uses the brain.

For **Claude Code** (the default) this installs the real advantage: a
``SessionStart`` hook that auto-injects the memory startup contract every
session, a ``Stop`` hook that enforces save-discipline, and the
``/sink-the-ship`` session-end skill. A rules file (the manual approach) is
not guaranteed to be loaded into context; a hook is.

For Windsurf / Cursor / Cline (``--client``) there is no hook system, so we
write the matching rules file with the memory protocol block.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import theme
from ._files import read_template as _read_template
from ._files import retire_file as _retire_file
from ._files import write_file as _write_file
from .gitignore import GITIGNORE_ENTRIES as GITIGNORE_ENTRIES
from .gitignore import ensure_gitignore
from .global_rules import init_global_rules, init_repo_rules
from .settings import (
    MINION_ALLOW,
    init_user_permissions,
    is_user_level,
    load_settings,
    merge_permissions,
    merge_settings,
    write_settings,
)

CLIENT_RULES_FILES = {
    "windsurf": ".windsurfrules",
    "cursor": ".cursorrules",
    "cline": ".clinerules",
}

_MCP_HINT = (
    "Next steps:\n"
    '  1. Register the Gingugu MCP server in your client under the name "gingugu":\n'
    "       claude mcp add gingugu -- gingugu\n"
    '     (or add it to your client\'s MCP config with the key "gingugu")\n'
    "  2. Restart your client so the SessionStart hook loads."
)


_HOME_NOTE = [
    "skip hooks: this is your home directory. Its .claude/settings.json is the",
    "  user-level file loaded in every project, so hooks wired there would block",
    "  prompts in any project without its own hooks.",
    "Run `gingugu init` inside each project to install its hooks.",
]


def init_claude_code(target: Path, *, force: bool, dry_run: bool, adopt: bool = False) -> list[str]:
    # State the resolved target first. `--path` defaults to the process's cwd,
    # and wrappers move that out from under you — `uv run --directory X` runs in
    # X, so a bare `gingugu init` there bootstraps X, not the directory you typed
    # the command in. Naming the path up front turns a silent wrong-repo write
    # into something you notice on line one.
    results: list[str] = ["Claude Code bootstrap:", f"  target {target}"]
    if is_user_level(target):
        # User-level steps only: the managed CLAUDE.md block and the minion
        # permission belong in ~/.claude; hooks, the skill, and .gitignore are
        # per-project.
        results.extend(_HOME_NOTE)
        results.append("")
        results.extend(init_global_rules(dry_run=dry_run, adopt=adopt))
        results.extend(init_user_permissions(dry_run=dry_run))
        results.append("")
        results.append(_MCP_HINT)
        return results

    hooks_dir = target / ".claude" / "hooks"
    skill_path = target / ".claude" / "skills" / "sink-the-ship" / "SKILL.md"
    legacy_command = target / ".claude" / "commands" / "sink-the-ship.md"

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
        hooks_dir / "pre_tool_tripwire.py",
        _read_template("pre_tool_tripwire.py.tmpl"),
        force=force,
        dry_run=dry_run,
        results=results,
    )
    _write_file(
        hooks_dir / "subagent_warmup.py",
        _read_template("subagent_warmup.py.tmpl"),
        force=force,
        dry_run=dry_run,
        results=results,
    )
    # `sink-the-ship` ships as a SKILL, not a `.claude/commands/*.md` slash
    # command. Anthropic documents the commands directory inside `skills.md` as
    # the predecessor format and says to prefer skills for new work: a command
    # is a flat single file, while a skill is a directory that can carry
    # supporting files which load only when they are actually opened.
    #
    # Shipping the legacy format was not merely dated — it pinned every repo
    # that installs us to it. A repo that converted its own copy to a skill got
    # the command resurrected on the next `gingugu init`, leaving two
    # definitions answering to one name.
    sink_the_ship = _read_template("sink-the-ship.md.tmpl")
    _write_file(
        skill_path,
        sink_the_ship,
        force=force,
        dry_run=dry_run,
        results=results,
    )
    # Clean up after ourselves rather than leaving the duplicate we used to
    # create. Only ever removes a copy carrying our marker, and always leaves a
    # .bak — see retire_file.
    _retire_file(
        legacy_command,
        pristine=sink_the_ship,
        dry_run=dry_run,
        results=results,
        superseded_by=".claude/skills/sink-the-ship/SKILL.md",
    )

    settings_path = target / ".claude" / "settings.json"
    raw = settings_path.read_text() if settings_path.exists() else None
    settings, added, warnings = merge_settings(load_settings(settings_path), hooks_dir=hooks_dir)
    if merge_permissions(settings):
        added = [*added, f"permissions.allow {MINION_ALLOW}"]
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

    ensure_gitignore(target, dry_run=dry_run, results=results)

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
    # Global minions (~/.claude/agents) run in every repo, so their permission
    # lives in the user-level settings too. Additive, backed up, idempotent.
    results.extend(init_user_permissions(dry_run=dry_run))

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
