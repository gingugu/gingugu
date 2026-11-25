"""X20 part 2: which repo a board post means, and where its checkout is here.

The repo token in ``xray: <repo> <range>`` is another agent's text, so it is
gated (`valid`) before it becomes part of a path, and a candidate checkout is
used only when its identity (origin ``owner/name``, else folder name) matches.
The search is ``<root>/<name>`` per root, then ``<root>/<group>/<name>`` for each
group folder (``groups``): no glob, no walk past that one level.
"""

from __future__ import annotations

import itertools
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from . import local, scan
from .scan import XrayError

_PART = re.compile(r"[A-Za-z0-9._-]{1,100}")
_DEFAULT_ROOTS = ("GIT", "git", "src", "code", "projects", "dev", "repos", "Developer")


def valid(name: str) -> bool:
    if not isinstance(name, str) or ".." in name:
        return False
    parts = name.split("/")
    return len(parts) <= 2 and all(_PART.fullmatch(p) and p[0] not in ".-" for p in parts)


def normalize(url: str) -> str | None:
    """``owner/name`` (lowercased, no ``.git``) from the last two path parts of a remote."""
    url = (url or "").strip()
    if "://" in url:
        path = urlparse(url).path
    elif ":" in url and not url.startswith("/"):
        path = url.split(":", 1)[1]  # scp-style git@host:owner/name.git
    else:
        path = url
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    parts[-1] = parts[-1].removesuffix(".git")
    name = "/".join(parts[-2:]).lower()
    return name if valid(name) else None


def of(root: Path, timeout: float = 3) -> str | None:
    """A checkout's identity: its origin, else its folder name."""
    try:
        proc = scan.git(root, "config", "--get", "remote.origin.url", timeout=timeout)
    except XrayError:
        proc = None
    if proc is not None and proc.returncode == 0:
        found = normalize(proc.stdout.decode(errors="replace").strip())
        if found:
            return found
    base = root.name.lower()
    return base if valid(base) else None


def matches(root: Path, want: str) -> bool:
    mine = of(root)
    if mine is None:
        return False
    want = want.lower()
    return mine == want if "/" in want else mine.rpartition("/")[2] == want


def roots() -> list[Path]:
    env = os.environ.get("MEMORY_XRAY_REPOS")
    if env is None:
        cands = [Path.home() / d for d in _DEFAULT_ROOTS]
    else:
        cands = [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    seen: set[tuple[int, int]] = set()
    out: list[Path] = []
    for c in cands:
        try:
            if not c.is_dir():
                continue
            st = c.stat()
        except OSError:
            continue
        # One folder, one entry: macOS folds case (~/GIT is ~/git) and resolve()
        # keeps the case it was given, so compare the inode, not the path.
        if (st.st_dev, st.st_ino) not in seen:
            seen.add((st.st_dev, st.st_ino))
            out.append(c)
    return out


MAX_SCAN = 2000  # entries read from one folder: a root full of downloads is no stall


def entries(base: Path) -> list[Path]:
    """Up to ``MAX_SCAN`` entries of ``base``, by name; [] when it cannot be read."""
    try:
        return sorted(itertools.islice(base.iterdir(), MAX_SCAN), key=lambda p: p.name)
    except OSError:
        return []


def is_checkout(p: Path) -> bool:
    try:
        return p.is_dir() and (p / ".git").exists()
    except OSError:
        return False


def groups(base: Path) -> list[Path]:
    """The group folders of a search root: real (not symlinked), non-dot folders
    that are not themselves checkouts, by name."""
    out = []
    for p in entries(base):
        try:
            if p.name.startswith(".") or p.is_symlink() or not p.is_dir():
                continue
        except OSError:
            continue
        if not is_checkout(p):
            out.append(p)
    return out


def find(want: str, cwd: str | None) -> Path | None:
    """The local checkout of ``want``, or None. Never raises."""
    try:
        if not valid(want):
            return None
        try:
            here = local.root(cwd)
            if cwd and matches(here, want):
                return here
        except (XrayError, ValueError, OSError):
            pass
        name = want.rpartition("/")[2]
        for r in roots():
            cand = r / name
            if (cand / ".git").exists() and cand.is_dir() and matches(cand, want):
                return cand
        for r in roots():
            for g in groups(r):
                cand = g / name
                try:
                    if (cand / ".git").exists() and cand.is_dir() and matches(cand, want):
                        return cand
                except OSError:
                    continue
    except Exception:  # noqa: BLE001 - a lookup must never cost the mail
        pass
    return None
