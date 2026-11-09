"""X22 in the hooks: which docs cite a file, from a small per-repo summary.

The docs map costs a git call per sibling checkout, too much for every hook
call. So the repo map on arrival refreshes ``xray-docs-in-<digest>.json`` when it
is older than ``FRESH_S`` (siblings on a short budget), and the read hook, the
edit note and the Bash X-ray read only that summary. Owner-only, read as
untrusted input like every X-ray cache. ``MEMORY_XRAY_DOCS=off`` silences it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import docs_map, hook_common

VERSION = 1
FRESH_S = 600
REFRESH_BUDGET_S = 1.0
SHOWN = 3  # named in a hook line
MAX_PER_FILE = 8  # rows kept per file
MAX_TARGETS = 2000  # files kept: what keeps the summary under MAX_BYTES
MAX_BYTES = 4 * 1024 * 1024
SHOWN_CHARS = 120
_STR = 1024


def _path(root: Path, state_dir: Path) -> Path:
    return state_dir / f"xray-docs-in-{hook_common._digest(root)}.json"


def _load(root: Path, state_dir: Path) -> dict | None:
    path = _path(root, state_dir)
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
    except OSError:
        return None
    raw = hook_common._read_json(path)
    if not isinstance(raw, dict) or raw.get("v") != VERSION or type(raw.get("at")) is not int:
        return None
    if not 0 <= time.time() - raw["at"] < 10 * FRESH_S + 86_400 * 30:
        return None  # from the future, or ancient: a summary that would never refresh
    return raw if isinstance(raw.get("files"), dict) else None


def refresh(root: Path, state_dir: Path) -> None:
    """Re-read the docs map when this repo's summary is stale. Never raises."""
    try:
        if not docs_map.enabled():
            return
        raw = _load(root, state_dir)
        if raw and time.time() - raw["at"] < FRESH_S:
            return
        m = docs_map.read(root, state_dir, budget_s=REFRESH_BUDGET_S)
        files: dict[str, list] = {}
        for rel, cites in docs_map.cited(m).items():
            if len(files) >= MAX_TARGETS:
                break
            files[rel] = [[c.doc, c.repo] for c in cites[:MAX_PER_FILE]]
        data = {"v": VERSION, "at": int(time.time()), "files": files}
        hook_common._write_json(_path(root, state_dir), data)
    except Exception:  # noqa: BLE001 - the hooks it serves never pay for a failure
        return


def _good(row: object) -> bool:
    return (
        type(row) is list
        and len(row) == 2
        and type(row[0]) is str
        and len(row[0]) <= _STR
        and (row[1] is None or (type(row[1]) is str and len(row[1]) <= _STR))
    )


_PLAIN = re.compile(r"[^\w./@+-]")


def _plain(text: str) -> str:
    """A name from someone else's checkout, as it reaches an agent's context: path
    characters only, cut short. A file can be named like an instruction."""
    text = _PLAIN.sub("?", text)
    return text if len(text) <= SHOWN_CHARS else text[:SHOWN_CHARS] + "..."


def _label(row: list) -> str:
    return _plain(row[0]) if row[1] is None else f"{_plain(row[1])}:{_plain(row[0])}"


def _list(rows: list) -> str:
    more = f" (+{len(rows) - SHOWN} more)" if len(rows) > SHOWN else ""
    return ", ".join(_label(r) for r in rows[:SHOWN]) + more


def _rows(root: Path, rel: str, state_dir: Path) -> list | None:
    if not docs_map.enabled():
        return None
    raw = _load(root, state_dir)
    rows = raw["files"].get(rel) if raw else None
    if not isinstance(rows, list):
        return None
    return [r for r in rows if _good(r)] or None


def file_line(root: Path, rel: str, state_dir: Path) -> str | None:
    """``Docs cite it: ...`` from the last refresh, else None. Reads only the summary."""
    rows = _rows(root, rel, state_dir)
    return f"Docs cite it: {_list(rows)} - code_xray docs names them all." if rows else None


def gone_line(root: Path, rel: str, state_dir: Path) -> str | None:
    """``Docs still cite it: ...`` for a deleted file, else None."""
    rows = _rows(root, rel, state_dir)
    return f"Docs still cite it: {_list(rows)} - code_xray docs names them all." if rows else None
