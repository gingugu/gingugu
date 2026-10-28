"""Read a repo's sources and manifests from disk, naming everything it declines to read.

The tree being scanned is untrusted: it may hold symlinks aimed outside the
repo, a source file too big to be real, or a git config that tries to run a
program. Every file we decline is returned with a reason rather than dropped
silently, because a graph that quietly omits files looks complete and is not.

Git is invoked with ``core.fsmonitor`` forced off and optional locks disabled.
A repo's own config can set ``core.fsmonitor`` to an arbitrary command that git
then runs on ``ls-files``/``status``; ``-c`` outranks the repo config, and a
read-only analysis has no business taking index locks.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
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


def is_manifest(rel: str, patterns: tuple[str, ...]) -> bool:
    """Whether ``rel``'s basename matches a manifest pattern, case-sensitively."""
    name = rel.rpartition("/")[2]
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


def read_sources(
    root: Path,
    max_bytes: int,
    extensions: tuple[str, ...],
    manifests: tuple[str, ...] = (),
    max_total: int | None = None,
) -> tuple[dict[str, bytes], dict[str, bytes], list[tuple[str, str]]]:
    """Return (sources, manifests, skipped) for every listed file the adapters want.

    ``sources`` are files ending in one of ``extensions``; ``manifests`` are
    files whose basename matches one of the ``manifests`` patterns (``fnmatch``,
    e.g. ``tsconfig*.json``), wherever they sit. Both map a repo-relative path
    to its bytes, and both pass the same checks. Past ``max_total`` bytes read,
    every further file is ``too_large``: the parse runs inside a hook.
    """
    total = 0
    specs = [f"*{ext}" for ext in extensions] + [f"*{name}" for name in manifests]
    proc = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *specs)
    if proc.returncode != 0:
        raise XrayError(f"git ls-files failed: {proc.stderr.decode().strip()}")
    sources: dict[str, bytes] = {}
    found: dict[str, bytes] = {}
    skipped: list[tuple[str, str]] = []
    for raw in sorted({p for p in proc.stdout.split(b"\0") if p}):
        rel = raw.decode("utf-8", errors="replace")
        if rel.endswith(extensions):
            into = sources
        elif is_manifest(rel, manifests):
            into = found
        else:
            continue  # a pathspec glob also matches e.g. "foogo.mod"
        if "\ufffd" in rel or "\\" in rel:
            # Git allows any bytes in a name; the brain takes only plain UTF-8 paths
            # and refuses a payload whole over one. Named (mangled), never shipped.
            skipped.append((rel.replace("\\", "\ufffd"), "unreadable"))
            continue
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
            size = full.stat().st_size
            if size > max_bytes or (max_total is not None and total + size > max_total):
                skipped.append((rel, "too_large"))
                continue
            into[rel] = full.read_bytes()
            total += size
        except OSError:
            skipped.append((rel, "unreadable"))
    return sources, found, skipped
