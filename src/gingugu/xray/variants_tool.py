"""``code_xray`` action ``variants``: how far this repo's flavours have drifted apart.

Compares tracked files with each sibling checkout whose name marks it a flavour of
this one (``variants.flavours``). Computed on this machine; only names and counts
leave it, never content. Shared by the stdio handler, the proxy and ``gingugu xray``.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from . import docs_map, history, local, scan, variants
from .history_tool import MAX_LIMIT, _fit
from .hook_common import _show

ACTIONS = ("variants",)
MAX_BYTES = 1_000_000
DEADLINE_S = 10.0
CAVEAT = (
    "Drift is changed lines over lines across the files both checkouts track: per file, "
    "the lines of the longer side that a line diff does not match (a binary file counts "
    "as one line). Flavours are found by name only - the same name once its last "
    "-segment is dropped - so a sibling that merely shares a prefix is listed too. "
    "Symlinks are never followed and files over 1 MB are not read. Computed on this "
    "machine; no file content leaves it, only names and counts."
)


def _tracked(repo: Path) -> set[str] | None:
    try:
        proc = scan.git(repo, "ls-files", "-z", "--cached", timeout=history.GIT_TIMEOUT_S)
    except (scan.XrayError, OSError):
        return None
    return set(docs_map._names(proc)) if proc.returncode == 0 else None


def _read(root: Path, rel: str) -> tuple[bytes | None, bool]:
    """(bytes, too big): (None, False) is a path to leave out entirely. The same
    open as ``docs_map._read_text``: a file swapped for a link or a FIFO after
    ``_inside`` checked its name is refused, never followed or blocked on."""
    full = docs_map._inside(root, rel)
    if full is None:
        return None, False
    try:
        if not hasattr(os, "O_NOFOLLOW") and os.path.islink(full):
            return None, False  # Windows has no O_NOFOLLOW: check, then open
        fd = os.open(full, docs_map._OPEN_FLAGS)
        with os.fdopen(fd, "rb") as handle:
            st = os.fstat(handle.fileno())
            if not stat.S_ISREG(st.st_mode):
                return None, False
            data = handle.read(MAX_BYTES + 1)
        return (None, True) if len(data) > MAX_BYTES else (data, False)
    except OSError:
        return None, False


def _compare(repo: Path, sib: Path, only: set[str] | None, stop: float) -> dict | None:
    """One flavour, scored a file at a time: only one pair is ever held, and past
    ``stop`` nothing more is read (``partial``)."""
    a, b = _tracked(repo), _tracked(sib)
    if a is None or b is None:
        return None
    if only is not None:
        a, b = a & only, b & only
    scored: dict[str, tuple[int, int, bool]] = {}
    unread, partial = 0, False
    for rel in sorted(a & b):
        if time.monotonic() > stop:
            partial = True
            break
        x, big_x = _read(repo, rel)
        y, big_y = _read(sib, rel)
        if big_x or big_y:
            unread += 1
        elif x is not None and y is not None:
            exact = time.monotonic() < stop
            scored[rel] = (*variants.file_drift(x, y, exact=exact), x == y)
    return {**variants.summary(scored, a - b, b - a), "unread": unread, "partial": partial}


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def render(rows: list[dict], name: str) -> str:
    """Compact text for an agent; every name and path is quoted by ``_show``."""
    lines = [f"GINGUGU X-RAY VARIANTS: {_show(name)}"]
    if not rows:
        lines.append("no sibling checkout is a flavour of this one")
        return _fit(lines)
    for r in rows:
        lines.append(
            f"{_show(r['name'])}: {round(r['drift'] * 100, 1)}% drift - {r['shared']} shared "
            f"({r['identical']} identical, {r['differing']} differing), "
            f"{r['only_here_total']} only here, {r['only_there_total']} only there"
            + (f", {r['unread']} unread" if r["unread"] else "")
            + (" (partial: out of time)" if r["partial"] else "")
        )
        lines += [
            f"- {_show(f['path'])}: {f['changed']} of {_n(f['lines'], 'line')} changed"
            for f in r["files"]
        ]
        for key, label in (("only_there", "only there"), ("only_here", "only here")):
            if r[key]:
                lines.append(f"  {label}: " + ", ".join(_show(p) for p in r[key]))
    return _fit(lines)


def answer(
    path: str | None = None,
    files=None,
    limit: int = 20,
    *,
    session_id: str | None = None,
) -> dict:
    """Each flavour's drift from the checkout at ``path``; ``files`` restricts the paths."""
    repo = local.root(path)
    named = local.file_list(files)
    only = {p for p in local.seeds(named, repo) if p} if named else None
    sibs = {c.name: c for c in docs_map._candidates(repo)}
    n = min(max(0, limit), MAX_LIMIT)
    stop = time.monotonic() + DEADLINE_S
    rows: list[dict] = []
    for name in variants.flavours(repo.name, sorted(sibs)):
        d = _compare(repo, sibs[name], only, stop)
        if d is None:
            continue
        rows.append(
            {
                "name": name,
                **{**d, "drift": round(d["drift"], 3)},
                "files": d["files"][:n],
                "only_here": d["only_here"][:n],
                "only_there": d["only_there"][:n],
                "only_here_total": len(d["only_here"]),
                "only_there_total": len(d["only_there"]),
            }
        )
    rows.sort(key=lambda r: (-r["drift"], r["name"]))
    return {"ok": True, "variants": rows, "text": render(rows, repo.name), "caveat": CAVEAT}
