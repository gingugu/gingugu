"""X15: a write made by a shell command still gets its X-ray.

The edit tripwire (X2) speaks before an ``Edit``/``Write``; a ``sed -i``, a
codegen script or a ``git checkout`` writes behind its back. So ``before``
snapshots each candidate checkout's dirty set (paths, status, content hash) when
a ``Bash`` call is about to run, and ``after`` diffs it once the call returns and
names what each changed source file reaches. Candidates are the command's cwd
repo, every repo the session has touched, and any repo a path in the command
points into. A deleted file's old importers come from the graph cached before
the command. It informs only - the write already happened - and shares the
edit hook's "already shown" claim so a file is reported once per session.

Like every hook it must never break a session: any failure returns None.
"""

from __future__ import annotations

import os
import shlex
import time
from pathlib import Path

from . import bash_state, edit_hook, hook_common, lang, local, rank
from .bash_state import remember, touched
from .hook_common import _claim, _show, _sweep
from .read_hook import _importers
from .scan import XrayError

__all__ = ["MAX_DIRTY", "MAX_REPOS", "after", "before", "remember", "touched"]

MAX_DIRTY = 500
MAX_REPOS = bash_state.MAX_REPOS
_MAX_TOKENS = 64
_BUDGET_S = 4.0  # snapshot time per Bash call, all repos together
_AFTER_BUDGET_S = 6.0  # the diff's share of the doorway's 10s
_MAX_COMMAND = 8192
_FULL = 5  # files described in full; the rest are counted
_NAMED = 3
# Claude Code caps additionalContext at 10,000 characters.
_MAX_CHARS = 8800


def _ours(payload: dict) -> tuple[str, str, str, Path] | None:
    """(session, tool_use_id, command, cwd) when this is a Bash call we watch."""
    if payload.get("tool_name") != "Bash" or not edit_hook.enabled():
        return None
    session_id, use_id = payload.get("session_id"), payload.get("tool_use_id")
    tool_input = payload.get("tool_input")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(use_id, str) or not use_id or not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    cwd = payload.get("cwd")
    return session_id, use_id, command, Path(cwd if isinstance(cwd, str) and cwd else os.getcwd())


def _root_of(where: Path) -> Path | None:
    try:
        return local.root(str(where))
    except (XrayError, ValueError, OSError):
        return None


def _command_roots(command: str, cwd: Path, found: list[Path], deadline: float) -> None:
    """Add the checkout of each token that names an existing path to ``found``,
    until the repo cap or the deadline: every lookup is a git call."""
    command = command[:_MAX_COMMAND]  # a path worth watching is near the front
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    # POSIX splitting eats the backslashes of a Windows path; the plain words keep them.
    tokens += [t for t in command.split() if t not in tokens]
    tried: set[Path] = set()
    for token in tokens[:_MAX_TOKENS]:
        if len(found) >= MAX_REPOS or time.monotonic() > deadline:
            return
        if not token or token.startswith("-"):
            continue
        try:
            path = Path(token) if Path(token).is_absolute() else cwd / token
            if not path.exists():
                continue
            where = path if path.is_dir() else path.parent
        except (OSError, ValueError):
            continue
        if where in tried:
            continue
        tried.add(where)
        root = _root_of(where)
        if root is not None and root not in found:
            found.append(root)


def _candidates(
    state_dir: Path, session_id: str, command: str, cwd: Path, deadline: float
) -> list[Path]:
    found: list[Path] = []
    here = _root_of(cwd)
    if here is not None:
        remember(state_dir, session_id, here)
        found.append(here)
    found.extend(r for r in touched(state_dir, session_id) if r not in found)
    _command_roots(command, cwd, found, deadline)
    return found[:MAX_REPOS]


def _snapshot_path(state_dir: Path, session_id: str, use_id: str) -> Path:
    return hook_common.claim_path(state_dir, "xray-bash", session_id, Path("/"), use_id)


def before(payload: dict, state_dir: Path) -> None:
    """Snapshot each candidate checkout's dirty set. Never builds a graph."""
    try:
        ours = _ours(payload)
        if ours is None:
            return None
        session_id, use_id, command, cwd = ours
        snapshot = {}
        # The PreToolUse hook has 15s for everything, memory tripwires included.
        deadline = time.monotonic() + _BUDGET_S
        for root in _candidates(state_dir, session_id, command, cwd, deadline):
            if time.monotonic() > deadline:
                break
            found = bash_state.dirty(root, MAX_DIRTY)
            if found is not None:
                snapshot[str(root)] = found
        if snapshot:
            hook_common._write_json(_snapshot_path(state_dir, session_id, use_id), snapshot)
    except Exception:  # noqa: BLE001 - a bug here never breaks a command
        pass
    return None


def _changes(root: Path, was: dict) -> tuple[list[str], list[str]] | None:
    """(changed, deleted) source paths since the snapshot, else None."""
    now = bash_state.dirty(root, MAX_DIRTY)
    if now is None:
        return None
    changed, deleted = [], []
    for rel in sorted(set(was) | set(now)):
        if lang.for_path(rel) is None:
            continue
        here = now[rel][1] if rel in now else bash_state.current_hash(root, rel)
        if rel in was and was[rel][1] == here:
            continue
        if rel not in was and rel not in now:
            continue
        if here is None:
            if rel not in was or was[rel][1] is not None:
                deleted.append(rel)  # it existed before the command
        else:
            changed.append(rel)
    return changed, deleted


def _names(paths: list[str]) -> str:
    listed = ", ".join(_show(p) for p in paths[:_NAMED])
    return listed + (f" (+{len(paths) - _NAMED} more)" if len(paths) > _NAMED else "")


def _deleted_lines(root: Path, state_dir: Path, session_id: str, deleted: list[str]) -> list[str]:
    old = hook_common.cached_graph(root, state_dir)  # as it stood, before any rebuild
    if old is None:
        return []
    importers = _importers(old)
    out = []
    for rel in deleted:
        who = sorted(importers.get(rel, ()) - set(deleted), key=lambda p: (-old.ranks.get(p, 0), p))
        claim = edit_hook._claim_path(state_dir, session_id, root, rel)
        if who and rel not in old.packages and not claim.exists() and _claim(claim):
            out.append(f"{_show(rel)} was deleted; it was imported by {len(who)}: {_names(who)}")
    return out


def _file_lines(root: Path, state_dir: Path, session_id: str, changed: list[str]) -> list[str]:
    out: list[str] = []
    if not changed:
        return out
    files, links, ranks, packages, _, _ = hook_common.graph(root, state_dir, session_id)
    full = 0
    for rel in changed:
        claim = edit_hook._claim_path(state_dir, session_id, root, rel)
        if rel not in files or rel in packages or claim.exists():
            continue
        if full >= _FULL:
            out.append("")  # a placeholder counted below
            continue
        blast = edit_hook._without_seed(
            rank.blast([rel], files, links, ranks, hidden=packages), rel
        )
        if blast["code_total"] + blast["tests_total"] == 0 or not _claim(claim):
            continue
        full += 1
        out.extend(edit_hook._dependents(rel, blast))
    more = out.count("")
    out = [line for line in out if line]
    if more:
        out.append(f"+{more} more changed source files not shown.")
    return out


def after(payload: dict, state_dir: Path) -> str | None:
    """What the files the command changed reach, else None."""
    try:
        ours = _ours(payload)
        if ours is None:
            return None
        session_id, use_id, _, _ = ours
        path = _snapshot_path(state_dir, session_id, use_id)
        snapshot = hook_common._read_json(path)
        path.unlink(missing_ok=True)  # consumed
        if not isinstance(snapshot, dict):
            return None
        sections = []
        deadline = time.monotonic() + _AFTER_BUDGET_S
        for raw, was in snapshot.items():
            if time.monotonic() > deadline:
                break
            root = Path(raw)
            if not isinstance(was, dict) or not root.is_dir():
                continue
            diff = _changes(root, was)
            if diff is None or not (diff[0] or diff[1]):
                continue
            changed, deleted = diff
            lines = _deleted_lines(root, state_dir, session_id, deleted)
            lines += _file_lines(root, state_dir, session_id, changed)
            if lines:
                head = f"that command changed source in {_show(root.name)}:"
                sections.append("\n".join([head, *lines]))
        if not sections:
            return None
        _sweep(state_dir)
        text = "GINGUGU X-RAY: " + "\n\n".join(sections)
        text += "\nImport-based estimate; the write already happened - check these callers."
        return text[:_MAX_CHARS]
    except Exception:  # noqa: BLE001 - a bug here never breaks a command
        return None
