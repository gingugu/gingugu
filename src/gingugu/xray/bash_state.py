"""What X15 remembers: the repos a session touched and a checkout's dirty set.

Split from ``bash_hook`` so the read and edit hooks can record a touched repo
without importing the hook that imports them. Everything here is cheap and
never raises: it runs on every Read and Edit, and before every Bash call.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from . import hook_common
from .scan import XrayError, git

MAX_REPOS = 8
# Hashed small, stat-compared big: this runs before and after every Bash call.
_HASH_LIMIT = 64 * 1024


def _dir(state_dir: Path, session_id: str) -> Path:
    return hook_common.claim_path(state_dir, "xray-repos", session_id, Path("/")).parent


def remember(state_dir: Path, session_id: str, root: Path) -> None:
    """Record that the session touched ``root``. One file per repo, because hooks
    run in parallel and a shared list would lose a record; re-writing bumps the
    mtime, which is what orders ``touched``."""
    try:
        path = hook_common.claim_path(state_dir, "xray-repos", session_id, root)
        hook_common._write_json(path, {"root": str(root)})
    except Exception:  # noqa: BLE001 - bookkeeping never breaks a read or an edit
        pass


def touched(state_dir: Path, session_id: str) -> list[Path]:
    """This session's recorded repos that still exist, most recent first."""
    found: list[tuple[float, Path]] = []
    try:
        for entry in _dir(state_dir, session_id).iterdir():
            if entry.is_symlink() or entry.name.endswith(".tmp"):
                continue
            data = hook_common._read_json(entry)
            raw = data.get("root") if isinstance(data, dict) else None
            if isinstance(raw, str) and Path(raw).is_dir():
                found.append((entry.stat().st_mtime, Path(raw)))
    except OSError:
        return []
    found.sort(key=lambda t: t[0], reverse=True)
    return [root for _, root in found[:MAX_REPOS]]


def current_hash(root: Path, rel: str) -> str | None:
    """sha256 of a small file, ``size:mtime_ns`` of a big one, None when missing.
    A symlink is compared by where it points, never followed out of the checkout."""
    path = root / rel
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            return "link:" + os.readlink(path)
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_size > _HASH_LIMIT:
            return f"{st.st_size}:{st.st_mtime_ns}"
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def dirty(root: Path, limit: int) -> dict[str, list] | None:
    """{repo-relative path: [status, content hash]} for what git calls dirty, or
    None when it cannot be read or holds more than ``limit`` paths."""
    try:
        proc = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=3)
    except XrayError:
        return None
    if proc.returncode != 0:
        return None
    parts = proc.stdout.split(b"\0")
    out: dict[str, list] = {}
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        status = entry[:2].decode("ascii", errors="replace").strip()
        rel = entry[3:].decode("utf-8", errors="replace")
        if status[:1] in ("R", "C") or entry[1:2] in (b"R", b"C"):
            i += 1  # the source path follows as its own NUL-separated field
        out[rel] = [status, current_hash(root, rel)]
        if len(out) > limit:
            return None
    return out
