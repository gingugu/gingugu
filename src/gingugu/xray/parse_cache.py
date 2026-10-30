"""A per-file parse cache, keyed by content, so a rebuild parses only new bytes.

Parsing is ~80% of a graph build and depends on nothing but a file's bytes and
extension (``Adapter.parse``); linking - path resolution, entry points,
manifests - is cheap and always rerun. So a cached parse can never go stale in
a way the fingerprint would not already catch, and an edit to one file costs one
parse, not the repo.

The file is ``{"header": {...}, "raws": {adapter: {slot: raw}}}``. A header that
differs at all (cache format, gingugu version, Python ``major.minor``, extractor
version) empties the cache: any of those can change what a parse returns. The
file is never trusted - a bad one reads as empty and a failed write is skipped,
because the answer is always rebuildable and must never depend on the cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from importlib import metadata
from pathlib import Path

FORMAT = 1
# Past this the cache costs more to read on every miss than it saves: a repo of
# tiny import-only files serialises to more than its sources. Not written then.
MAX_BYTES = 64_000_000
# Parsers whose output a cached parse depends on; pinned by range, not by gingugu.
_PARSERS = ("tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript", "tree-sitter-go")

Raws = dict[str, dict[str, object]]


def slot(ext: str, data: bytes) -> str:
    """Extension and content: the same bytes as ``.ts`` and ``.tsx`` parse differently.
    ``ext`` is the adapter extension the name ends with, not ``Path.suffix``: a file
    named just ``.tsx`` has no suffix, and its grammar is still chosen by ending."""
    return f"{ext}:{hashlib.sha256(data).hexdigest()}"


def header(extractor_version: int) -> dict:
    import gingugu  # read at call time: an upgrade in a long-lived process must show

    return {
        "format": FORMAT,
        "gingugu": gingugu.__version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "extractor": extractor_version,
        "parsers": {name: _version(name) for name in _PARSERS},
    }


def _version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def load(path: str | Path, extractor_version: int) -> Raws:
    """Cached raws, or empty on any problem or header mismatch. Never raises."""
    try:
        data = json.loads(Path(path).read_text())
        if data["header"] != header(extractor_version):
            return {}
        return {str(a): dict(e) for a, e in data["raws"].items() if isinstance(e, dict)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return {}


def save(path: str | Path, extractor_version: int, raws: Raws) -> None:
    """Owner-only, renamed into place whole, so a parallel build never reads half a
    file and a link planted at the temp name is refused. Silently skipped when it
    cannot be written (``hook_common._write_json`` is the same dance)."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        text = json.dumps({"header": header(extractor_version), "raws": raws})
        if len(text) > MAX_BYTES:
            path.unlink(missing_ok=True)  # an older, smaller copy would be read for nothing
            return
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
