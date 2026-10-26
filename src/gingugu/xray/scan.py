"""Read a repo's Python sources from disk, naming everything it declines to read.

The tree being scanned is untrusted: it may hold symlinks aimed outside the
repo, a ``.py`` too big to be real source, or a git config that tries to run a
program. Every file we decline is returned with a reason rather than dropped
silently, because a graph that quietly omits files looks complete and is not.

Git is invoked with ``core.fsmonitor`` forced off and optional locks disabled.
A repo's own config can set ``core.fsmonitor`` to an arbitrary command that git
then runs on ``ls-files``/``status``; ``-c`` outranks the repo config, and a
read-only analysis has no business taking index locks.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path


class XrayError(Exception):
    """The path cannot be X-rayed (not a git repo, git missing, unreadable)."""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise XrayError(f"cannot run git in {cwd}: {exc}") from exc


def toplevel(path: str | Path) -> Path:
    proc = git(Path(path), "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        raise XrayError(f"not a git repository: {path}")
    return Path(proc.stdout.decode().strip()).resolve()


def commit(root: Path) -> str | None:
    proc = git(root, "rev-parse", "--verify", "-q", "HEAD")
    if proc.returncode != 0:
        return None  # unborn branch
    return proc.stdout.decode().strip() or None


def read_sources(root: Path, max_bytes: int) -> tuple[dict[str, bytes], list[tuple[str, str]]]:
    """Return ({path: bytes}, [(path, reason)]) for every listed ``.py`` file."""
    proc = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py")
    if proc.returncode != 0:
        raise XrayError(f"git ls-files failed: {proc.stderr.decode().strip()}")
    sources: dict[str, bytes] = {}
    skipped: list[tuple[str, str]] = []
    for rel in sorted({p for p in proc.stdout.decode().split("\0") if p}):
        full = root / rel
        if full.is_symlink():
            skipped.append((rel, "symlink"))
            continue
        if not full.is_file():  # --cached still lists a deleted tracked file
            continue
        if not full.resolve().is_relative_to(root):  # e.g. a parent dir is a symlink
            skipped.append((rel, "outside"))
            continue
        try:
            if full.stat().st_size > max_bytes:
                skipped.append((rel, "too_large"))
                continue
            sources[rel] = full.read_bytes()
        except OSError:
            skipped.append((rel, "unreadable"))
    return sources, skipped


def _entry_targets(data: dict) -> set[str]:
    project = data.get("project", {})
    targets: list[object] = []
    if isinstance(project, dict):
        for table in ("scripts", "gui-scripts"):
            group = project.get(table, {})
            targets += list(group.values()) if isinstance(group, dict) else []
        groups = project.get("entry-points", {})
        for group in groups.values() if isinstance(groups, dict) else []:
            targets += list(group.values()) if isinstance(group, dict) else []
    return {t.split(":")[0].strip() for t in targets if isinstance(t, str)}


def load_pyproject(root: Path) -> tuple[bytes | None, str | None, set[str]]:
    """Return (bytes hashed, skip reason, entry-point module names)."""
    path = root / "pyproject.toml"
    if path.is_symlink():
        return None, "symlink", set()
    if not path.exists():
        return None, None, set()
    try:
        data = path.read_bytes()
    except OSError:
        return None, "unreadable", set()
    try:
        return data, None, _entry_targets(tomllib.loads(data.decode("utf-8")))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return data, "malformed", set()
