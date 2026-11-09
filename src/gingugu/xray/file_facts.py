"""The cache plumbing shared by the per-file fact maps (``env_map``, ``table_map``).

A fact map lists the checkout's files, scans each once for named facts (a few
``[name, line]`` lists under fixed keys), and caches the result per file by size
and mtime. The cache is a hint, not a truth: it is read as untrusted input, and a
malformed entry is dropped and re-scanned. Client side only; the brain is never asked.

The caller passes a ``Spec`` built at call time, so a module's own ``scan_text``,
``MAX_FILE_BYTES`` and ``MAX_NAMES`` are looked up when they are used, not bound here.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from . import hook_common, scan
from .docs_map import _inside, _names, _read_text

MAX_FILES = 20000
MAX_CACHE_BYTES = 16 * 1024 * 1024
GIT_TIMEOUT_S = 10
VERSION = 1

Refs = dict[str, list[tuple[str, int]]]


class Spec(NamedTuple):
    keys: tuple[str, ...]  # the fact lists an entry holds, in scan order
    cache_prefix: str  # cache file: <prefix>-<digest of root>.json
    kind: Callable[[str], str | None]
    scan: Callable[[str, str], tuple]  # (rel, text) -> one list of (name, line) per key
    valid: Callable[[object], bool]  # a stored name
    max_bytes: int  # a file past this is skipped entirely
    max_names: int  # per file, per key


def names_in(refs: Refs, rel: str) -> list[str]:
    """The names ``rel`` appears under in ``refs``, in the order of their first line."""
    found = sorted((line, name) for name, locs in refs.items() for f, line in locs if f == rel)
    out: list[str] = []
    for _, name in found:
        if name not in out:
            out.append(name)
    return out


def _pairs(value: object, spec: Spec) -> list[list] | None:
    if not isinstance(value, list) or len(value) > spec.max_names:
        return None
    for item in value:
        if not (isinstance(item, list) and len(item) == 2 and spec.valid(item[0])):
            return None
        if type(item[1]) is not int or item[1] < 1:
            return None
    return value


def load(path: Path, spec: Spec) -> tuple[dict[str, dict], object]:
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
        lists = {k: _pairs(e.get(k), spec) for k in spec.keys}
        if any(v is None for v in lists.values()):
            continue
        out[rel] = {"size": e["size"], "mtime": e["mtime"], **lists}
    return out, raw


def _entry(root: Path, rel: str, old: dict, spec: Spec) -> dict | None:
    full = _inside(root, rel)
    if full is None:
        return None
    try:
        st = full.stat()
    except OSError:
        return None
    if st.st_size > spec.max_bytes:
        return None
    prev = old.get(rel)
    if prev and prev["size"] == st.st_size and prev["mtime"] == st.st_mtime_ns:
        return prev
    try:
        text = _read_text(full)
    except OSError:
        return None
    found = spec.scan(rel, text)
    return {
        "size": st.st_size,
        "mtime": st.st_mtime_ns,
        **{
            k: [[n, line] for n, line in f[: spec.max_names]]
            for k, f in zip(spec.keys, found, strict=True)
        },
    }


def read(
    root: Path, state_dir: Path, spec: Spec, *, budget_s: float | None = None
) -> tuple[tuple[Refs, ...], bool]:
    """(one ``Refs`` per key, complete) for the checkout at ``root``, from the cache
    where it holds. With ``budget_s``, files not reached in time keep their cached
    entry, or wait for the next call; what was read is written either way, so the
    cache fills."""
    proc = scan.git(
        root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", timeout=GIT_TIMEOUT_S
    )
    if proc.returncode != 0:
        raise scan.XrayError("git ls-files failed")
    names = [n for n in _names(proc) if spec.kind(n) is not None][:MAX_FILES]
    cache = state_dir / f"{spec.cache_prefix}-{hook_common._digest(root)}.json"
    old, raw = load(cache, spec)
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
        entry = _entry(root, rel, old, spec)
        if entry is not None:
            files[rel] = entry
    fresh = {"v": VERSION, "files": files}
    if fresh != raw:
        hook_common._write_json(cache, fresh)
    out: tuple[Refs, ...] = tuple({} for _ in spec.keys)
    for rel, e in files.items():
        for into, key in zip(out, spec.keys, strict=True):
            for name, line in e[key]:
                into.setdefault(name, []).append((rel, line))
    return out, complete
