"""Helpers the X-ray hooks share: state files, the cached graph, path resolution.

Both the edit hook (PreToolUse) and the read hook (PostToolUse) answer from the
same per-repo graph cache and the same owner-only state directory, so neither
builds a graph the other already paid for.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import NamedTuple

from . import extract, local, rank
from .scan import XrayError

_STATE_TTL_SECONDS = 7 * 24 * 3600


def _write_json(path: Path, data: object) -> bool:
    """Owner-only, written whole to a fresh temp file and renamed into place,
    so a parallel hook never reads half a file and a link planted at the temp
    name is refused. False when it could not be written."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data))
        os.replace(tmp, path)
        return True
    except OSError:
        tmp.unlink(missing_ok=True)
        return False


def _sweep(state_dir: Path) -> None:
    """Drop X-ray state untouched for a week, as ``tool_hook`` does for tripwires."""
    import shutil

    cutoff = time.time() - _STATE_TTL_SECONDS
    try:
        for entry in state_dir.glob("xray-*"):
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def _show(path: str) -> str:
    """A repo path as the agent reads it. Git allows newlines and control
    characters in names; quoted, a planted name cannot start a line of its own."""
    return path if path.isprintable() else json.dumps(path)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def resolve_file(raw: str, cwd: object) -> tuple[Path, str] | None:
    """(repo root, repo-relative path) for a file path from a tool call, else None."""
    cwd_path = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
    abs_path = Path(raw) if Path(raw).is_absolute() else cwd_path / raw
    # A new file's directory may not exist yet; the checkout still answers.
    where = abs_path.parent if abs_path.parent.is_dir() else cwd_path
    try:
        repo = local.root(str(where))
        return repo, local.seeds([str(abs_path)], repo)[0]
    except (XrayError, ValueError, OSError):
        return None


def claim_path(state_dir: Path, prefix: str, session_id: str, root: Path, key: str = "") -> Path:
    """One file per claim: ``<prefix>-<session>/<digest of repo and key>``."""
    from ..subagent_hook import safe_session

    name = hashlib.sha256(f"{root}\0{key}".encode()).hexdigest()[:32]
    return state_dir / f"{prefix}-{safe_session(session_id)}" / name


def _claim(path: Path) -> bool:
    """Create the claim atomically. False if another hook got there first, or
    if it cannot be written - a deny that is not recorded would never lift."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        os.close(os.open(path, flags, 0o600))
        return True
    except OSError:
        return False


class Graph(NamedTuple):
    files: dict[str, bool]  # path -> is_test
    links: list[rank.Link]
    ranks: dict[str, float]
    packages: set[str]
    langs: dict[str, str]  # path -> language
    entries: set[str]


def graph(root: Path, state_dir: Path) -> Graph:
    """The checkout's graph, cached by fingerprint."""
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    cache = state_dir / f"xray-graph-{digest}.json"
    fp = extract.fingerprint(root)
    data = _read_json(cache)
    try:
        if isinstance(data, dict) and data["fingerprint"] == fp:
            return Graph(
                {str(k): bool(v) for k, v in data["files"].items()},
                [rank.Link(s, d, bool(c), bool(i)) for s, d, c, i in data["links"]],
                {str(k): float(v) for k, v in data["ranks"].items()},
                {str(p) for p in data["packages"]},
                {str(k): str(v) for k, v in data["langs"].items()},
                {str(p) for p in data["entries"]},
            )
    except (KeyError, TypeError, ValueError, AttributeError):
        pass  # a corrupt or older-format cache is rebuilt, not fatal
    ex = extract.extract(root)
    files = {f.path: f.is_test for f in ex.files}
    links = [rank.Link(e.src, e.dst, e.certain, e.implicit) for e in ex.edges]
    ranks = rank.pagerank(sorted(files), links)
    packages = {f.path for f in ex.files if f.kind == "package"}
    langs = {f.path: f.lang for f in ex.files}
    entries = {f.path for f in ex.files if f.is_entry}
    _write_json(
        cache,
        {
            "fingerprint": ex.fingerprint,
            "files": files,
            "links": [[ln.src, ln.dst, ln.certain, ln.implicit] for ln in links],
            "ranks": ranks,
            "packages": sorted(packages),
            "langs": langs,
            "entries": sorted(entries),
        },
    )
    return Graph(files, links, ranks, packages, langs, entries)
