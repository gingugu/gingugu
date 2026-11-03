"""X23: read this checkout and its siblings into facts, under a time budget.

Each repo's tracked files (plus untracked ones for this checkout) that
``model.wanted`` keeps are read and handed to the extractors one file at a
time; a file's facts are cached by size and mtime (``cache``), so a second read
of an unchanged repo costs a stat per file. Siblings are the checkouts next to
this one (``docs_map._candidates``), each known by its origin ``owner/name``,
and are read on a few threads: most of a warm read is git and stat calls,
which release the GIL.

Siblings are other people's checkouts: a name from their index is used only
when it is a regular file inside the checkout with no link anywhere on its way
there, the cache is read as untrusted input, and past the time budget a repo
keeps what its cache holds and says how many files it did not read. Nothing
reaches the brain.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from .. import repo_id, scan
from ..brief import _safe
from ..docs_map import _candidates, _names, _read_text
from . import cache, model, walk
from .cache import EXTRACT_VERSION, MAX_CACHE_BYTES, MAX_STR  # noqa: F401 - re-exported
from .join import RepoFacts
from .model import Fact

MAX_FILES = 8000  # per repo, after ``wanted``
MAX_FACTS_PER_FILE = 2000
MAX_NAMES_PER_FILE = 200  # resource-shaped literals are the bulk of any repo
# Per repo: what one sibling can hand the join, whatever its files hold. The loose
# kinds (globbed names, wildcard hosts, routes ending in a parameter) are tried
# against many mentions each, so they get the tightest cap.
MAX_FACTS_PER_REPO = 100_000
MAX_NAMES_PER_REPO = 20_000
MAX_LOOSE_PER_REPO = 500
GIT_TIMEOUT_S = 10

Extract = Callable[[str, str], tuple[list[Fact], list[Fact]]]
_cache_path = cache.path_for  # the names the tests and callers already use
_Inside = walk.Inside
_load = cache.load


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
        if f.kind in (model.NAME, model.REPO_NAME):  # the two kinds every file is full of
            names += 1
            if names > MAX_NAMES_PER_REPO:
                continue
        if _loose(f):
            loose += 1
            if loose > MAX_LOOSE_PER_REPO:
                continue
        out.append(f)
    return out


def _assemble(entries: cache.Entries, files: frozenset[str]) -> RepoFacts:
    ordered = [entries[n] for n in sorted(entries)]
    return RepoFacts(
        _bounded([f for e in ordered for f in e[2]]),
        _bounded([f for e in ordered for f in e[3]]),
        files,
    )


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
    cached_ident, old = cache.load(root, state_dir)
    inside = walk.Inside(root)
    entries: cache.Entries = {}
    unread = 0
    for name in [n for n in names if model.wanted(n)][:MAX_FILES]:
        prev = old.get(name)
        if time.monotonic() >= deadline:  # past the budget: not even a stat
            unread += 1
            if prev:
                entries[name] = prev  # stale beats nothing; still counted unread
            continue
        found = inside(name)
        if found is None:
            continue
        full, st = found
        if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
            entries[name] = prev
            continue
        if walk.Inside(root)(name) is None:
            continue  # checked again, uncached, right before it is opened
        try:
            provided, mentioned = extract(name, _read_text(full))
        except OSError:
            continue
        except Exception:  # noqa: BLE001 - one odd file never costs the repo
            provided, mentioned = [], []
        entries[name] = (st.st_size, st.st_mtime_ns, _cap(provided), _cap(mentioned))
    if entries != old or cached_ident != ident:
        cache.save(root, state_dir, ident, entries)
    return _assemble(entries, frozenset(names)), unread


def _folder(child: Path) -> str:
    return _safe(child.name)


def _sibling(child: Path, me: str, state_dir: Path, deadline: float, extract: Extract):
    """(ident, checkout, facts, unread), ("unscanned", folder), or None (skip it).
    Past the budget no git is run: a cached sibling is served unchecked."""
    try:
        if time.monotonic() >= deadline:
            ident, entries = cache.load(child, state_dir)
            if ident == me:
                return None
            if not ident or not repo_id.valid(ident) or not entries:
                return ("unscanned", _folder(child))
            return (ident, child, _assemble(entries, frozenset(entries)), len(entries))
        left = max(0.2, min(3.0, deadline - time.monotonic()))
        ident = repo_id.of(child, timeout=left) or child.name.lower()
        if ident == me or not repo_id.valid(ident):
            return None  # this repo elsewhere (a worktree, a clone), or a bad name
        try:
            facts, left = scan_repo(
                child, state_dir, deadline, own=False, extract=extract, ident=ident
            )
        except (scan.XrayError, OSError):
            return ("unscanned", _folder(child))
        if left and not (facts.provided or facts.mentioned):
            return ("unscanned", _folder(child))
        return (ident, child, facts, left)
    except Exception:  # noqa: BLE001 - one odd sibling never costs the read
        return ("unscanned", _folder(child))


def read(
    root: Path, state_dir: Path, *, budget_s: float, extract: Extract | None = None
) -> Ecosystem:
    """This checkout and every sibling, within ``budget_s`` of reading. Past the
    budget no sibling is asked anything: one with a cache is served from it
    unchecked (every cached file counted as unread), one without is unscanned.
    Two checkouts of one repo: the first, in folder order, is the one read."""
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
    children = _candidates(root)
    results = walk.gather(
        children, lambda c: _sibling(c, me, state_dir, deadline, extract), deadline
    )
    for child, res in zip(children, results, strict=True):  # folder order: duplicates resolve alike
        if res is walk.LATE:
            unscanned.append(_folder(child))
            continue
        if res is None:
            continue
        if res[0] == "unscanned":
            unscanned.append(res[1])
            continue
        ident, child, facts, left = res
        if ident in repos:
            continue
        repos[ident], roots[ident] = facts, child
        if left:
            partial[ident] = left
    return Ecosystem(me, roots, repos, partial, tuple(unscanned))
