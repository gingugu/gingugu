"""Helpers the X-ray hooks share: state files, the cached graph, path resolution.

Both the edit hook (PreToolUse) and the read hook (PostToolUse) answer from the
same per-repo graph cache and the same owner-only state directory, so neither
builds a graph the other already paid for.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import NamedTuple

from . import extract, local, rank
from . import symbols as _sym
from .scan import XrayError

_STATE_TTL_SECONDS = 7 * 24 * 3600
# Past every hook timeout (doorway 10s, settings 15s): a build marker this old was killed.
_ABANDONED_SECONDS = 30


def _write_json(path: Path, data: object) -> bool:
    """Owner-only, written whole to a fresh temp file and renamed into place,
    so a parallel hook never reads half a file and a link planted at the temp
    name is refused. False when it could not be written."""
    # pid and thread: the proxy builds briefs on worker threads of one process.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
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
            if entry.lstat().st_mtime >= cutoff:  # a dangling link must not stop the sweep
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
    except (OSError, ValueError, RecursionError):  # deep nesting is not a ValueError
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


def _digest(root: Path) -> str:
    return hashlib.sha256(str(root).encode()).hexdigest()[:16]


def _cache_path(root: Path, state_dir: Path) -> Path:
    return state_dir / f"xray-graph-{_digest(root)}.json"


def _load_cache(cache: Path, fingerprint: str | None) -> Graph | None:
    """The cached graph; with a ``fingerprint``, only if it still matches."""
    data = _read_json(cache)
    try:
        if isinstance(data, dict) and (fingerprint is None or data["fingerprint"] == fingerprint):
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
    return None


def cached_graph(root: Path, state_dir: Path) -> Graph | None:
    """The graph as last cached, fingerprint unchecked: what the checkout looked
    like before something changed it. None when nothing was cached."""
    return _load_cache(_cache_path(root, state_dir), None)


def _symbols_path(root: Path, state_dir: Path) -> Path:
    return state_dir / f"xray-symbols-{_digest(root)}.json"


def _load_symbols(cache: Path, fingerprint: str) -> _sym.Symbols | None:
    """The cached symbol graph, only if it is this checkout's and well-formed."""
    data = _read_json(cache)
    if not isinstance(data, dict) or data.get("fingerprint") != fingerprint:
        return None
    return _sym.from_json({k: v for k, v in data.items() if k != "fingerprint"})


def _build(root: Path, state_dir: Path, session_id: str | None) -> tuple[Graph, _sym.Symbols]:
    """One extraction, both caches written. With a session, a build that never
    finished - the doorway's timeout killed it - is not started again in that
    session: a checkout too big to build in time would otherwise pay the whole
    build on every tool call."""
    marker = claim_path(state_dir, "xray-build", session_id, root) if session_id else None
    if marker is not None and not _claim(marker):
        # A fresh marker is a parallel hook mid-build: build too, as before. One
        # older than any hook may run is a build that was killed.
        try:
            age = time.time() - marker.lstat().st_mtime
        except OSError:
            age = 0.0
        if age >= _ABANDONED_SECONDS:
            raise XrayError("a graph build for this checkout did not finish this session")
    ex = extract.extract(root, parse_cache=state_dir / f"xray-parse-{_digest(root)}.json")
    files = {f.path: f.is_test for f in ex.files}
    links = [rank.Link(e.src, e.dst, e.certain, e.implicit) for e in ex.edges]
    ranks = rank.pagerank(sorted(files), links)
    packages = {f.path for f in ex.files if f.kind == "package"}
    langs = {f.path: f.lang for f in ex.files}
    entries = {f.path for f in ex.files if f.is_entry}
    _write_json(
        _cache_path(root, state_dir),
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
    _write_json(
        _symbols_path(root, state_dir),
        {"fingerprint": ex.fingerprint, **_sym.to_json(ex.symbols)},
    )
    if marker is not None:
        marker.unlink(missing_ok=True)
    return Graph(files, links, ranks, packages, langs, entries), ex.symbols


def graph(
    root: Path, state_dir: Path, session_id: str | None = None, fingerprint: str | None = None
) -> Graph:
    """The checkout's graph, cached by fingerprint (see ``_build`` for the marker).

    A hook that needs the graph and the symbols passes one ``fingerprint`` to
    both: hashing the checkout is the warm path's whole cost (0.37s on django)."""
    fp = fingerprint or extract.fingerprint(root)
    cached = _load_cache(_cache_path(root, state_dir), fp)
    return cached if cached is not None else _build(root, state_dir, session_id)[0]


def symbols(
    root: Path, state_dir: Path, session_id: str | None = None, fingerprint: str | None = None
) -> _sym.Symbols:
    """The checkout's symbol graph (who calls what), cached beside the import
    graph by the same fingerprint; one build writes both."""
    fp = fingerprint or extract.fingerprint(root)
    cached = _load_symbols(_symbols_path(root, state_dir), fp)
    return cached if cached is not None else _build(root, state_dir, session_id)[1]
