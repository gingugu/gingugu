"""``gingugu harness`` - the Claude Code harness, installed on top of ``gingugu init``.

Plain ``init`` stays gingugu-only. ``harness`` runs ``init`` and then adds the
rest of the kit: a safety guard and event logging, four fenced minions, the
``creating-pr`` skill, the ``.ai/`` knowledge base, and a managed block in the
repo's ``CLAUDE.md``. Nothing it installs calls out to the network.

Ownership rules, in one place:

- hooks, agents and skill files are *managed*: written through ``write_file``
  (skip if present, ``--force`` overwrites, a ``.bak`` whenever bytes change);
- ``.ai/`` files are the user's content once created: written only when missing,
  never overwritten, never backed up;
- ``CLAUDE.md`` is the user's file: only the text between our markers is ours.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import tui
from ..prompt_hook import _PERSONA_RE as NAMESPACE_RE
from . import global_rules, init_claude_code
from ._files import read_template, safe_read, write_file
from .harness_claude_md import HARNESS_BEGIN as HARNESS_BEGIN  # re-exported for callers
from .harness_claude_md import HARNESS_END as HARNESS_END
from .harness_claude_md import _apply_claude_md
from .harness_global import install_delegation
from .harness_migrate import migrate_settings, retire_legacy
from .harness_prune import prune
from .harness_settings import init_user_subagent_model, wire_harness_settings
from .harness_shipped import render, stock_check
from .settings import _HOOKS as _SETTINGS_HOOKS
from .settings import is_user_level, load_settings, write_settings

_T = "harness/"
_MANAGED = [
    ("hooks/log_event.py", ".claude/hooks/log_event.py"),
    ("hooks/pre_tool_use.py", ".claude/hooks/pre_tool_use.py"),
    ("hooks/pre_compact.py", ".claude/hooks/pre_compact.py"),
    ("agents/repo-scout.md", ".claude/agents/repo-scout.md"),
    ("agents/security-reviewer.md", ".claude/agents/security-reviewer.md"),
    ("agents/ai-docs-auditor.md", ".claude/agents/ai-docs-auditor.md"),
    ("agents/researcher.md", ".claude/agents/researcher.md"),
    ("skills/creating-pr/SKILL.md", ".claude/skills/creating-pr/SKILL.md"),
    (
        "skills/creating-pr/ai-assessment-checklist.md",
        ".claude/skills/creating-pr/ai-assessment-checklist.md",
    ),
    ("skills/creating-pr/stacking-prs.md", ".claude/skills/creating-pr/stacking-prs.md"),
]
# The hooks init wires, read from its own table so a new doorway is listed once.
_INIT_HOOKS = tuple(entry[3] for entry in _SETTINGS_HOOKS)
# Every hook --migrate overwrites with ours: init's plus the harness's own.
_HOOKS = frozenset([*_INIT_HOOKS, *(Path(d).name for _, d in _MANAGED if "/hooks/" in d)])
_AI_FILES = (
    "memory.md",
    "plans/status.md",
    "specs/01-architecture.md",
    "specs/product-spec.md",
    "specs/dataflow.md",
    "standards/01-code-and-testing.md",
)


def minion_grant(namespace: str) -> str:
    """The minions' grant: read ``crow`` and this repo, write only ``minions``.

    A repo named ``crow`` is listed once - a namespace granted twice is refused.
    """
    reads = ["crow"] if namespace == "crow" else ["crow", namespace]
    return ",".join([*(f"{name}=read" for name in reads), "minions=write"])


def _install_managed(
    target: Path,
    namespace: str,
    *,
    force: bool,
    dry_run: bool,
    results: list[str],
    force_hooks: bool = False,
) -> None:
    """``force_hooks`` force-replaces only hook scripts (``--migrate``).

    That covers init's hooks too: migrate runs init without force so init's
    skill (``sink-the-ship``, which a repo may have made its own) is never
    replaced, then forces init's hooks here.
    """
    grant = minion_grant(namespace)

    def write(dest: Path, content: str, forced: bool, rel: str = "") -> None:
        if forced and force_hooks and dest.is_symlink():  # never write through a link
            results.append(f"  skip   {dest}  (a symlink; not replaced)")
            return
        # A stock copy of an older version is refreshed without --force.
        stock = stock_check(rel, namespace, grant) if rel else None
        write_file(dest, content, force=forced, dry_run=dry_run, results=results, is_stock=stock)

    if force_hooks and not force:
        for name in _INIT_HOOKS:
            write(target / ".claude" / "hooks" / name, read_template(f"{name}.tmpl"), True)
    for template, dest in _MANAGED:
        content = render(read_template(f"{_T}{template}.tmpl"), namespace, grant)
        forced = force or (force_hooks and dest.startswith(".claude/hooks/"))
        write(target / dest, content, forced, template)


def _scaffold_ai(target: Path, *, dry_run: bool, results: list[str]) -> None:
    """Create each ``.ai/`` file only if missing - it is the user's content."""
    for rel in _AI_FILES:
        path = target / ".ai" / rel
        if path.exists():
            results.append(f"  skip   {path}  (exists; never overwritten)")
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(read_template(f"{_T}ai/{rel}.tmpl"))
        results.append(f"  {'would write' if dry_run else 'write':<9} {path}")


def _settings_object(raw: str | None) -> bool:
    """True when there is no settings file, or it holds a JSON object."""
    if raw is None:
        return True
    try:
        return isinstance(json.loads(raw), dict)
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gingugu harness",
        description="Run `gingugu init`, then install the Claude Code harness into a project.",
    )
    parser.add_argument("--path", default=".", help="Target repo directory (default: current dir)")
    parser.add_argument("--force", action="store_true", help="Overwrite managed files that exist")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would happen, write nothing"
    )
    parser.add_argument(
        "--adopt",
        action="store_true",
        help="Hand your own memory-protocol / delegation sections to gingugu (backed up)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--migrate",
        action="store_true",
        help="Also replace our hooks, unwire an older per-event hook kit, retire its scripts",
    )
    mode.add_argument(
        "--prune",
        action="store_true",
        help="Only delete the stubs and retired files a --migrate left behind",
    )
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser().resolve()
    if not target.is_dir():
        print(f"error: target path is not a directory: {target}")
        return 1
    if is_user_level(target):
        print("error: `gingugu harness` installs hooks and agents, so run it inside a project.")
        return 1
    namespace = target.name
    if not NAMESPACE_RE.fullmatch(namespace):
        print(
            f"error: the directory name {namespace!r} cannot be a memory namespace "
            "(letters, digits, '.', '_', '-'; must start with a letter or digit; max 64)."
        )
        return 1
    if namespace == "minions":
        print("error: 'minions' is the minions' scratch namespace; it cannot be a repo's.")
        return 1

    if args.prune:
        results = ["Claude Code harness prune:", *prune(target, dry_run=args.dry_run)]
        tui.report(results, dry_run=args.dry_run, command="harness", subject=target.name)
        return 0

    settings_path = target / ".claude" / "settings.json"
    settings_existed = settings_path.exists()
    settings_start = settings_path.read_text() if settings_existed else None
    if args.migrate and not _settings_object(settings_start):
        print(f"error: {settings_path} is not a JSON object; fix it before --migrate.")
        return 1

    claude_md = target / "CLAUDE.md"
    original = safe_read(claude_md) if claude_md.exists() else None
    user_md = global_rules.global_claude_md()  # init may rewrite (and back up) it before we do
    user_original = safe_read(user_md) if user_md.exists() else None

    migrated: list[str] = []
    if args.migrate:
        # Before init, so init's additive merge sees the cleaned settings.
        settings = load_settings(settings_path)
        hooks_dir = target / ".claude" / "hooks"
        migrated = migrate_settings(settings, hooks_dir, dry_run=args.dry_run)
        if migrated and not args.dry_run:
            # Back up first: a later step can fail, and the original must survive it.
            (settings_path.parent / "settings.json.bak").write_text(settings_start or "")
            write_settings(settings_path, settings)

    # create_rules: init writes a missing CLAUDE.md, so the harness block below lands in it.
    results = init_claude_code(
        target, force=args.force, dry_run=args.dry_run, adopt=args.adopt, create_rules=True
    )
    if migrated and not args.dry_run:
        # init backs settings up too - from the already-migrated file. Restore ours.
        (settings_path.parent / "settings.json.bak").write_text(settings_start or "")
    project_settings = str(target / ".claude" / "settings.json")
    init_backed_up = any(
        project_settings in line and "settings.json.bak" in line for line in results
    )

    results.extend(["", "Claude Code harness:"])
    _install_managed(
        target,
        namespace,
        force=args.force,
        dry_run=args.dry_run,
        results=results,
        force_hooks=args.migrate,
    )
    _scaffold_ai(target, dry_run=args.dry_run, results=results)
    _apply_claude_md(target, original, dry_run=args.dry_run, results=results)
    lines, _ = wire_harness_settings(
        target,
        dry_run=args.dry_run,
        user_original_kept=init_backed_up or not settings_existed or args.migrate,
    )
    results.extend([*lines, "", "Claude Code harness (user level):"])
    results.extend(init_user_subagent_model(dry_run=args.dry_run))
    results.extend(
        install_delegation(
            dry_run=args.dry_run, adopt=args.adopt, force=args.force, original=user_original
        )
    )

    if args.migrate:
        if settings_start is not None and not args.dry_run:
            if settings_path.read_text() != settings_start:  # the user's original, once
                (settings_path.parent / "settings.json.bak").write_text(settings_start)
        report = [*migrated, *retire_legacy(target, dry_run=args.dry_run, replaced=_HOOKS)]
        report = report or ["  nothing to migrate - already clean"]
        results.extend(["", "Claude Code harness migrate:", *report])

    from ..claude_trust import untrusted_note

    note = untrusted_note(target.resolve())
    if note:
        results.extend(["", "Claude Code trust:", f"  warn   {note}"])
    tui.report(results, dry_run=args.dry_run, command="harness", subject=target.name)
    return 0
