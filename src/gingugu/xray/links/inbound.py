"""X23 in the hooks: who outside leans on a file, from a small per-repo summary.

A hook cannot afford a links read on every file read, so every links read
leaves an answer behind for each checkout it read: ``xray-links-in-<digest>.json``,
the links into that repo grouped by the file they point at (a few per file),
plus the repo-level ones. The read hook names a file's outside dependents the
first time it is read, the map on arrival names the count, and the edit note
names them after an edit. The repo map refreshes the summary when it is older
than ``FRESH_S``, on a short budget: past it, siblings come from their cache.

Owner-only, read as untrusted input like every X-ray cache.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .. import hook_common
from .model import LINK_KINDS, Link

VERSION = 1
PER_FILE = 8  # links kept per target file
WHOLE = 20  # repo-level links kept
SHOWN = 3  # named in a hook line
FRESH_S = 600
REFRESH_BUDGET_S = 1.0
MAX_BYTES = 4 * 1024 * 1024
MAX_TARGETS = 2000  # target files kept per summary: what keeps it under MAX_BYTES
SHOWN_CHARS = 80
_STR = 300


def _path(root: Path, state_dir: Path) -> Path:
    return state_dir / f"xray-links-in-{hook_common._digest(root)}.json"


def _row(lk: Link) -> list:
    return [lk.src, lk.src_path, lk.src_line, lk.kind, lk.key, lk.certain]


def write_all(roots: dict[str, Path], links: list[Link], state_dir: Path) -> None:
    """One summary per checkout read, certain links first."""
    by_dst: dict[str, list[Link]] = {}
    for lk in links:
        by_dst.setdefault(lk.dst, []).append(lk)
    now = int(time.time())
    for ident, root in roots.items():
        into = sorted(by_dst.get(ident, []), key=lambda lk: (not lk.certain, lk.src, lk.src_path))
        files: dict[str, list] = {}
        for lk in into:
            if lk.dst_path is None or (lk.dst_path not in files and len(files) >= MAX_TARGETS):
                continue
            if len(files.setdefault(lk.dst_path, [])) < PER_FILE:
                files[lk.dst_path].append(_row(lk))
        whole = [_row(lk) for lk in into if lk.dst_path is None][:WHOLE]
        data = {
            "v": VERSION, "repo": ident, "at": now, "total": len(into),
            "repos": len({lk.src for lk in into}), "files": files, "whole": whole,
        }  # fmt: skip
        hook_common._write_json(_path(root, state_dir), data)


def _load(root: Path, state_dir: Path) -> dict | None:
    path = _path(root, state_dir)
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
    except OSError:
        return None
    raw = hook_common._read_json(path)
    if not isinstance(raw, dict) or raw.get("v") != VERSION:
        return None
    if not all(type(raw.get(k)) is int for k in ("at", "total", "repos")):
        return None
    if not 0 <= time.time() - raw["at"] < 10 * FRESH_S + 86_400 * 30:
        return None  # from the future, or ancient: a summary that would never refresh
    if not isinstance(raw.get("files"), dict) or not isinstance(raw.get("whole"), list):
        return None
    return raw


def _good(row: object) -> bool:
    return (
        type(row) is list
        and len(row) == 6
        and all(type(v) is str and len(v) <= _STR for v in (row[0], row[1], row[3], row[4]))
        and type(row[2]) is int
        and type(row[5]) is bool
        and row[3] in LINK_KINDS
    )


_PLAIN = re.compile(r"[^\w./@+-]")


def _plain(text: str) -> str:
    """A name from someone else's checkout, as it reaches an agent's context: path
    characters only, cut short. A file can be named like an instruction."""
    text = _PLAIN.sub("?", text)
    return text if len(text) <= SHOWN_CHARS else text[:SHOWN_CHARS] + "..."


def _name(row: list) -> str:
    mark = "" if row[5] else "? "
    return f"{mark}{_plain(row[0])} {_plain(row[1])}:{row[2]} ({row[3]})"


def file_line(root: Path, rel: str, state_dir: Path) -> str | None:
    """``Other repos lean on it: ...`` for a file outside files point at, else None."""
    raw = _load(root, state_dir)
    rows = raw["files"].get(rel) if raw else None
    if not isinstance(rows, list):
        return None
    rows = [r for r in rows if _good(r)]
    if not rows:
        return None
    shown = ", ".join(_name(r) for r in rows[:SHOWN])
    more = f" (+{len(rows) - SHOWN} more)" if len(rows) > SHOWN else ""
    return (
        f"Other repos lean on it: {shown}{more} (names from other checkouts here; "
        "code_xray links has them all)."
    )


def map_line(root: Path, state_dir: Path) -> str | None:
    raw = _load(root, state_dir)
    if not raw or not raw["total"]:
        return None
    n, k = raw["total"], raw["repos"]
    return (
        f"Other repos lean on this one: {n} link{'' if n == 1 else 's'} from {k} "
        f"checkout{'' if k == 1 else 's'} here (code_xray links: who, by what, where)."
    )


def refresh(root: Path, state_dir: Path) -> None:
    """Re-read links on a short budget when this repo's summary is stale. Never
    raises: the hooks it serves never pay for a failure."""
    from . import index, join, tool

    try:
        if not tool.enabled():
            return
        raw = _load(root, state_dir)
        if raw and time.time() - raw["at"] < FRESH_S:
            return
        eco = index.read(root, state_dir, budget_s=REFRESH_BUDGET_S)
        write_all(eco.roots, join.join(eco.repos).links, state_dir)
    except Exception:  # noqa: BLE001
        return
