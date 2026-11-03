"""X23: the per-checkout facts cache - written owner-only, read as untrusted input.

One ``xray-links-<digest>.json`` per checkout in the hook-sessions state dir:
``{"v": <extractor digest>, "ident": "<owner/name>", "files": {path: [size,
mtime_ns, provided rows, mentioned rows]}}``. The version is a digest of the
extractors' own source, so any change to how facts are read retires every cached
fact with no number to remember to bump. A row that is out of shape drops its
file's entry; a cache too big or of another version is empty.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import hook_common
from . import model
from .model import Fact

MAX_STR = 1000  # any string read back from the cache
MAX_CACHE_BYTES = 32 * 1024 * 1024
_KINDS = frozenset(model.PROVIDED) | frozenset(model.MENTIONED)

Entries = dict[str, tuple[int, int, list[Fact], list[Fact]]]


def _extractor_version() -> str:
    here = Path(__file__).parent
    h = hashlib.sha256()
    for name in sorted(p.name for p in here.glob("*.py")):
        if name.startswith(("model", "provides", "mentions", "prose")):
            h.update(name.encode() + b"\0" + (here / name).read_bytes())
    return h.hexdigest()[:16]


EXTRACT_VERSION = _extractor_version()


def path_for(root: Path, state_dir: Path) -> Path:
    return state_dir / f"xray-links-{hook_common._digest(root)}.json"


def facts(raw: object, path: str) -> list[Fact] | None:
    if not isinstance(raw, list):
        return None
    out = []
    append = out.append
    for row in raw:  # plain checks, no generators: a warm read validates ~60k rows
        if type(row) is not list or len(row) != 5:
            return None
        kind, key, line, detail, target = row
        if (
            type(key) is not str
            or type(detail) is not str
            or type(target) is not str
            or kind not in _KINDS
            or type(line) is not int
            or not 0 < line < 10**8
            or len(key) > MAX_STR
            or len(detail) > MAX_STR
            or len(target) > MAX_STR
        ):
            return None
        append(Fact(kind, key, path, line, detail, target))
    return out


def load(root: Path, state_dir: Path) -> tuple[str | None, Entries]:
    """(identity, path -> (size, mtime_ns, provided, mentioned))."""
    path = path_for(root, state_dir)
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None, {}
    except OSError:
        return None, {}
    raw = hook_common._read_json(path)
    if not isinstance(raw, dict) or raw.get("v") != EXTRACT_VERSION:
        return None, {}
    ident = raw.get("ident") if isinstance(raw.get("ident"), str) else None
    files = raw.get("files")
    if not isinstance(files, dict):
        return ident, {}
    out: Entries = {}
    for name, e in files.items():
        if type(name) is not str or len(name) > MAX_STR or type(e) is not list or len(e) != 4:
            continue
        size, mtime, p, m = e
        if type(size) is not int or type(mtime) is not int:
            continue
        pf, mf = facts(p, name), facts(m, name)
        if pf is not None and mf is not None:
            out[name] = (size, mtime, pf, mf)
    return ident, out


def _row(f: Fact) -> list:
    return [f.kind, f.key, f.line, f.detail, f.target]


def save(root: Path, state_dir: Path, ident: str, entries: Entries) -> None:
    files = {
        n: [s, t, [_row(f) for f in p], [_row(f) for f in m]] for n, (s, t, p, m) in entries.items()
    }
    data = {"v": EXTRACT_VERSION, "ident": ident, "files": files}
    hook_common._write_json(path_for(root, state_dir), data)
