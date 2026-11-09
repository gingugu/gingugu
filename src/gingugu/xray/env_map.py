"""X7a: the env map - which files read an environment variable, and which set it.

Nothing imports an environment variable, so the code reading ``DB_URL`` and the
compose file, manifest, workflow, Dockerfile or ``.env.example`` that sets it are
invisible to each other in the import graph. ``scan_text`` (``env_scan``) finds
literal names in known forms; this module runs it over the checkout and caches
the result per file. Names and line numbers only: no value is ever kept, and a
real dotenv file is never opened.

Client side only: the brain is never asked. The cache is a hint, not a truth:
it is read as untrusted input, and a malformed entry is dropped and re-scanned.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import NamedTuple

from . import hook_common, scan
from .docs_map import _inside, _names, _read_text
from .env_scan import NAME, kind, scan_text, valid_name  # noqa: F401

MAX_FILES = 20000
MAX_FILE_BYTES = 262_144  # a file past this is skipped entirely
MAX_NAMES = 200  # per file, reads and sets each
MAX_CACHE_BYTES = 16 * 1024 * 1024
GIT_TIMEOUT_S = 10
VERSION = 1

Refs = dict[str, list[tuple[str, int]]]


class EnvMap(NamedTuple):
    reads: Refs
    sets: Refs
    complete: bool = True  # False: the budget ran out before every file was read


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_ENV", "on").lower() not in ("0", "off", "false")


def names_in(refs: Refs, rel: str) -> list[str]:
    """The names ``rel`` appears under in ``refs``, in the order of their first line."""
    found = sorted((line, name) for name, locs in refs.items() for f, line in locs if f == rel)
    out: list[str] = []
    for _, name in found:
        if name not in out:
            out.append(name)
    return out


def _pairs(value: object) -> list[list] | None:
    if not isinstance(value, list) or len(value) > MAX_NAMES:
        return None
    for item in value:
        if not (isinstance(item, list) and len(item) == 2 and valid_name(item[0])):
            return None
        if type(item[1]) is not int or item[1] < 1:
            return None
    return value


def _load(path: Path) -> tuple[dict[str, dict], object]:
    """(entries, raw) from the cache, every field type-checked."""
    try:
        too_big = path.stat().st_size > MAX_CACHE_BYTES
    except OSError:
        too_big = False
    raw = None if too_big else hook_common._read_json(path)
    if (
        not isinstance(raw, dict)
        or raw.get("v") != VERSION
        or not isinstance(raw.get("files"), dict)
    ):
        return {}, raw
    out: dict[str, dict] = {}
    for rel, e in raw["files"].items():
        if not isinstance(e, dict) or not all(type(e.get(k)) is int for k in ("size", "mtime")):
            continue
        reads, sets = _pairs(e.get("reads")), _pairs(e.get("sets"))
        if reads is None or sets is None:
            continue
        out[rel] = {"size": e["size"], "mtime": e["mtime"], "reads": reads, "sets": sets}
    return out, raw


def _entry(root: Path, rel: str, old: dict) -> dict | None:
    full = _inside(root, rel)
    if full is None:
        return None
    try:
        st = full.stat()
    except OSError:
        return None
    if st.st_size > MAX_FILE_BYTES:
        return None
    prev = old.get(rel)
    if prev and prev["size"] == st.st_size and prev["mtime"] == st.st_mtime_ns:
        return prev
    try:
        text = _read_text(full)
    except OSError:
        return None
    reads, sets = scan_text(rel, text)
    return {
        "size": st.st_size,
        "mtime": st.st_mtime_ns,
        "reads": [[n, line] for n, line in reads[:MAX_NAMES]],
        "sets": [[n, line] for n, line in sets[:MAX_NAMES]],
    }


def read(root: Path, state_dir: Path, *, budget_s: float | None = None) -> EnvMap:
    """The env map for the checkout at ``root``, from the cache where it holds.
    With ``budget_s``, files not reached in time keep their cached entry, or wait
    for the next call; what was read is written either way, so the cache fills."""
    proc = scan.git(
        root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", timeout=GIT_TIMEOUT_S
    )
    if proc.returncode != 0:
        raise scan.XrayError("git ls-files failed")
    names = [n for n in _names(proc) if kind(n) is not None][:MAX_FILES]
    cache = state_dir / f"xray-env-{hook_common._digest(root)}.json"
    old, raw = _load(cache)
    files: dict[str, dict] = {}
    deadline = None if budget_s is None else time.monotonic() + budget_s
    complete = True
    root = root.resolve()  # once, not per file
    for rel in names:
        if (
            deadline is not None and time.monotonic() >= deadline
        ):  # >=: a coarse clock (Windows) must not let budget 0 read
            if rel in old:
                files[rel] = old[rel]  # unchecked this call
            else:
                complete = False
            continue
        entry = _entry(root, rel, old)
        if entry is not None:
            files[rel] = entry
    fresh = {"v": VERSION, "files": files}
    if fresh != raw:
        hook_common._write_json(cache, fresh)
    reads: Refs = {}
    sets: Refs = {}
    for rel, e in files.items():
        for into, key in ((reads, "reads"), (sets, "sets")):
            for name, line in e[key]:
                into.setdefault(name, []).append((rel, line))
    return EnvMap(reads, sets, complete)
