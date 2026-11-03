"""X23: read this checkout and its siblings into facts, under a time budget.

Each repo's tracked files (plus untracked ones for this checkout) that
``model.wanted`` keeps are read and handed to the extractors one file at a
time; a file's facts are cached by size and mtime, so a second read of an
unchanged repo costs a stat per file. Siblings are the checkouts next to this
one (``docs_map._candidates``), each known by its origin ``owner/name``.

Siblings are other people's checkouts: a name from their index is used only
when it is a regular file inside the checkout, the cache is read as untrusted
input, and past the time budget a repo keeps what its cache holds and says how
many files it did not read. Nothing reaches the brain.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from .. import hook_common, repo_id, scan
from ..brief import _safe
from ..docs_map import _candidates, _inside, _names, _read_text
from . import model
from .join import RepoFacts
from .model import Fact


def _extractor_version() -> str:
    """A digest of the extractors' own source: any change to how facts are read
    retires every cached fact, with no version number to remember to bump."""
    here = Path(__file__).parent
    h = hashlib.sha256()
    for name in sorted(p.name for p in here.glob("*.py")):
        if name.startswith(("model", "provides", "mentions", "prose")):
            h.update(name.encode() + b"\0" + (here / name).read_bytes())
    return h.hexdigest()[:16]


EXTRACT_VERSION = _extractor_version()
MAX_FILES = 8000  # per repo, after ``wanted``
MAX_FACTS_PER_FILE = 2000
MAX_NAMES_PER_FILE = 200  # resource-shaped literals are the bulk of any repo
# Per repo: what one sibling can hand the join, whatever its files hold. The loose
# kinds (globbed names, wildcard hosts, routes ending in a parameter) are tried
# against many mentions each, so they get the tightest cap.
MAX_FACTS_PER_REPO = 100_000
MAX_NAMES_PER_REPO = 20_000
MAX_LOOSE_PER_REPO = 500
MAX_STR = 1000  # any string read back from the cache
MAX_CACHE_BYTES = 32 * 1024 * 1024
GIT_TIMEOUT_S = 10
_KINDS = frozenset(model.PROVIDED) | frozenset(model.MENTIONED)

Extract = Callable[[str, str], tuple[list[Fact], list[Fact]]]


class Ecosystem(NamedTuple):
    me: str  # this checkout's identity
    roots: dict[str, Path]  # identity -> checkout
    repos: dict[str, RepoFacts]
    partial: dict[str, int]  # identity -> files left unread (budget, or cache only)
    unscanned: tuple[str, ...]  # sibling folder names with nothing read or cached


def _default_extract(path: str, text: str) -> tuple[list[Fact], list[Fact]]:
    from . import mentions, provides

    return provides.extract({path: text}), mentions.extract({path: text})


def _cap(facts: list[Fact]) -> list[Fact]:
    names = 0
    out = []
    for f in facts[:MAX_FACTS_PER_FILE]:
        if f.kind == model.NAME:
            names += 1
            if names > MAX_NAMES_PER_FILE:
                continue
        out.append(f)
    return out


# --- the cache -------------------------------------------------------------------


def _cache_path(root: Path, state_dir: Path) -> Path:
    return state_dir / f"xray-links-{hook_common._digest(root)}.json"


def _facts(raw: object, path: str) -> list[Fact] | None:
    if not isinstance(raw, list):
        return None
    out = []
    for row in raw:
        if not (isinstance(row, list) and len(row) == 5):
            return None
        kind, key, line, detail, target = row
        strs = (kind, key, detail, target)
        if not all(isinstance(v, str) and len(v) <= MAX_STR for v in strs):
            return None
        if kind not in _KINDS or type(line) is not int or not 0 < line < 10**8:
            return None
        out.append(Fact(kind, key, path, line, detail, target))
    return out


def _load(root: Path, state_dir: Path) -> tuple[str | None, dict]:
    """(identity, path -> (size, mtime_ns, provided, mentioned)); a bad entry is
    dropped, a bad or other-version cache is empty."""
    path = _cache_path(root, state_dir)
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
    out = {}
    for name, e in files.items():
        if not (isinstance(name, str) and len(name) <= MAX_STR):
            continue
        if not (isinstance(e, list) and len(e) == 4):
            continue
        size, mtime, p, m = e
        if type(size) is not int or type(mtime) is not int:
            continue
        pf, mf = _facts(p, name), _facts(m, name)
        if pf is not None and mf is not None:
            out[name] = (size, mtime, pf, mf)
    return ident, out


def _row(f: Fact) -> list:
    return [f.kind, f.key, f.line, f.detail, f.target]


def _save(root: Path, state_dir: Path, ident: str, entries: dict) -> None:
    files = {
        n: [s, t, [_row(f) for f in p], [_row(f) for f in m]] for n, (s, t, p, m) in entries.items()
    }
    data = {"v": EXTRACT_VERSION, "ident": ident, "files": files}
    hook_common._write_json(_cache_path(root, state_dir), data)


def _loose(f: Fact) -> bool:
    return (
        (f.kind == model.RESOURCE and "*" in f.key)
        or (f.kind == model.HOST and f.key.startswith("*."))
        or (f.kind == model.ROUTE and f.key.endswith("/" + model.WILD))
    )


def _bounded(facts: list[Fact]) -> list[Fact]:
    """A repo's facts within the per-repo caps, first files first."""
    out, names, loose = [], 0, 0
    for f in facts:
        if len(out) >= MAX_FACTS_PER_REPO:
            break
        if f.kind == model.NAME:
            names += 1
            if names > MAX_NAMES_PER_REPO:
                continue
        if _loose(f):
            loose += 1
            if loose > MAX_LOOSE_PER_REPO:
                continue
        out.append(f)
    return out


def _assemble(entries: dict, files: frozenset[str]) -> RepoFacts:
    ordered = [entries[n] for n in sorted(entries)]
    return RepoFacts(
        _bounded([f for e in ordered for f in e[2]]),
        _bounded([f for e in ordered for f in e[3]]),
        files,
    )


# --- reading one repo ----------------------------------------------------------------


def _listing(root: Path, own: bool, timeout: float) -> list[str]:
    args = ["ls-files", "-z", "--cached"] + (["--others", "--exclude-standard"] if own else [])
    proc = scan.git(root, *args, timeout=timeout)
    if proc.returncode != 0:
        raise scan.XrayError("git ls-files failed")
    return _names(proc)


def scan_repo(
    root: Path,
    state_dir: Path,
    deadline: float,
    *,
    own: bool,
    extract: Extract,
    ident: str = "",
) -> tuple[RepoFacts, int]:
    """(facts, files left unread). Raises ``scan.XrayError`` when git cannot list it."""
    names = _listing(root, own, max(0.5, min(GIT_TIMEOUT_S, deadline - time.monotonic())))
    _cached_ident, old = _load(root, state_dir)
    entries: dict[str, tuple[int, int, list[Fact], list[Fact]]] = {}
    unread = 0
    for name in [n for n in names if model.wanted(n)][:MAX_FILES]:
        full = _inside(root, name)
        if full is None:
            continue
        try:
            st = full.stat()
        except OSError:
            continue
        prev = old.get(name)
        if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
            entries[name] = prev
            continue
        if time.monotonic() >= deadline:
            unread += 1
            if prev:
                entries[name] = prev  # stale beats nothing; still counted unread
            continue
        try:
            provided, mentioned = extract(name, _read_text(full))
        except OSError:
            continue
        except Exception:  # noqa: BLE001 - one odd file never costs the repo
            provided, mentioned = [], []
        entries[name] = (st.st_size, st.st_mtime_ns, _cap(provided), _cap(mentioned))
    if entries != old or _cached_ident != ident:
        _save(root, state_dir, ident, entries)
    return _assemble(entries, frozenset(names)), unread


def _folder(child: Path) -> str:
    return _safe(child.name)


def read(
    root: Path, state_dir: Path, *, budget_s: float, extract: Extract | None = None
) -> Ecosystem:
    """This checkout and every sibling, within ``budget_s`` of reading. Past the
    budget no sibling is asked anything: one with a cache is served from it
    unchecked (every cached file counted as unread), one without is unscanned."""
    extract = extract or _default_extract
    deadline = time.monotonic() + budget_s
    me = repo_id.of(root) or "this-repo"
    roots: dict[str, Path] = {me: root}
    repos: dict[str, RepoFacts] = {}
    partial: dict[str, int] = {}
    unscanned: list[str] = []
    repos[me], left = scan_repo(root, state_dir, deadline, own=True, extract=extract, ident=me)
    if left:
        partial[me] = left
    for child in _candidates(root):
        if time.monotonic() >= deadline:
            ident, entries = _load(child, state_dir)
            if ident == me or ident in repos:
                continue
            if not ident or not repo_id.valid(ident) or not entries:
                unscanned.append(_folder(child))
                continue
            repos[ident], roots[ident] = _assemble(entries, frozenset(entries)), child
            partial[ident] = len(entries)
            continue
        try:
            ident = repo_id.of(child) or child.name.lower()
        except Exception:  # noqa: BLE001
            continue
        if ident == me or ident in repos or not repo_id.valid(ident):
            continue  # this repo elsewhere (a worktree, a clone), a duplicate, a bad name
        try:
            facts, left = scan_repo(
                child, state_dir, deadline, own=False, extract=extract, ident=ident
            )
        except (scan.XrayError, OSError):
            unscanned.append(_folder(child))
            continue
        if left and not (facts.provided or facts.mentioned):
            unscanned.append(_folder(child))
            continue
        repos[ident], roots[ident] = facts, child
        if left:
            partial[ident] = left
    return Ecosystem(me, roots, repos, partial, tuple(unscanned))
