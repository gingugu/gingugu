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
from importlib.resources import files
from pathlib import Path

from . import theme
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


def _read_template(name: str) -> str:
    return (files("gingugu.bootstrap") / "templates" / name).read_text()


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


def _write_file(
    path: Path, content: str, *, force: bool, dry_run: bool, results: list[str]
) -> None:
    if path.exists() and not force:
        results.append(f"  skip   {path}  (exists; use --force to overwrite)")
        return
    verb = "would write" if dry_run else ("overwrite" if path.exists() else "write")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    results.append(f"  {verb:<9} {path}")


def init_claude_code(target: Path, *, force: bool, dry_run: bool) -> list[str]:
    results: list[str] = ["Claude Code bootstrap:"]
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
        commands_dir / "sink-the-ship.md",
        _read_template("sink-the-ship.md.tmpl"),
        force=force,
        dry_run=dry_run,
        results=results,
    )

    settings_path = target / ".claude" / "settings.json"
    raw = settings_path.read_text() if settings_path.exists() else None
    settings, added = merge_settings(load_settings(settings_path))
    if added:
        if not dry_run:
            if raw is not None:
                (target / ".claude" / "settings.json.bak").write_text(raw)
            write_settings(settings_path, settings)
        note = " (backed up existing to settings.json.bak)" if raw is not None else ""
        verb = "would wire" if dry_run else "wired"
        results.append(f"  {verb} {', '.join(added)} in {settings_path}{note}")
    else:
        results.append(f"  settings.json already wired (no change) {settings_path}")

    _ensure_gitignore(target, dry_run=dry_run, results=results)

    results.append("")
    results.append(_MCP_HINT)
    return results


def init_rules_file(client: str, target: Path, *, force: bool, dry_run: bool) -> list[str]:
    results: list[str] = [f"{client} bootstrap:"]
    rules_path = target / CLIENT_RULES_FILES[client]
    protocol = _read_template("rules_protocol.md.tmpl")

    if rules_path.exists() and not force:
        results.append(
            f"  skip   {rules_path}  (exists; use --force to overwrite). "
            "Paste the Memory Protocol section yourself, or re-run with --force."
        )
    else:
        verb = "would write" if dry_run else "write"
        if not dry_run:
            rules_path.write_text(protocol)
        results.append(f"  {verb} {rules_path}")

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
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser().resolve()
    if not target.is_dir():
        print(f"error: target path is not a directory: {target}")
        return 1

    if args.client == "claude-code":
        results = init_claude_code(target, force=args.force, dry_run=args.dry_run)
    else:
        results = init_rules_file(args.client, target, force=args.force, dry_run=args.dry_run)

    print(theme.render(results, dry_run=args.dry_run))
    return 0
