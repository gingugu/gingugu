"""X4: the repository's history as a second graph - churn, authors, co-change.

The import graph says what leans on what; the history says what actually moves
together. Read from ``git log`` on the caller's machine, once per HEAD, and
cached beside the import graph in the hook state directory. Nothing is sent to
the brain.

**Code-maat's numbers, not ours.** A commit touching more than
``MAX_CHANGESET`` files (a reformat, a rename sweep, a vendored drop) counts
toward each file's churn but not toward coupling, and a pair is kept only when
it shared ``MIN_SHARED`` commits: both are code-maat's published defaults
(``cmd_line.clj``), as is the rest of the coupling test in ``hotspots``.

**Renames are followed.** The log is read newest first, and a rename maps the
old name onto today's, so a file's history survives a move.

**No identities.** Authors are compared within the parser and only counted;
neither the cache nor any answer holds a name or an address.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import NamedTuple

from . import hook_common, scan

# The last WINDOW non-merge commits: a bound on the read, reported in answers.
WINDOW = 1000
MAX_CHANGESET = 30
MIN_SHARED = 5
GIT_TIMEOUT_S = 10
_HEADER = b"\x01"
_FORMAT = "--format=%x01%H%x1f%aE"  # %aE: the author as the repo's mailmap names them


class Commit(NamedTuple):
    author: str
    files: frozenset[str]


@dataclass(frozen=True)
class History:
    head: str | None
    commits: int  # commits read
    bulk: int  # commits past MAX_CHANGESET, left out of coupling
    churn: dict[str, int]  # path -> commits that touched it
    authors: dict[str, int]  # path -> distinct authors
    pairs: dict[tuple[str, str], int]  # (a, b) with a < b -> shared commits, >= MIN_SHARED


EMPTY = History(None, 0, 0, {}, {}, {})


def _text(raw: bytes) -> str:
    """Decoded as ``scan`` decodes ``ls-files``, so a history path matches its graph path."""
    return raw.decode("utf-8", errors="replace")


def parse(out: bytes) -> list[Commit]:
    """``git log -z --name-status`` output, newest first, renames followed.

    Read as a sequence: a header only where a status may start, so a path that
    holds the header byte is still a path. Truncated output ends quietly."""
    toks = out.split(b"\0")
    alias: dict[str, str] = {}
    commits: list[Commit] = []
    author: str | None = None
    files: set[str] = set()
    i = 0
    while i < len(toks):
        tok = toks[i].removeprefix(b"\n")
        if tok.startswith(_HEADER):
            if author is not None:
                commits.append(Commit(author, frozenset(files)))
            _, _, who = tok.partition(b"\x1f")
            author, files = _text(who), set()
            i += 1
            continue
        code = tok[:1]
        if author is None or code not in b"ACDMRTUX" or not code:
            i += 1
            continue
        if code in (b"R", b"C"):
            if i + 2 >= len(toks):
                break
            old, new = _text(toks[i + 1]), _text(toks[i + 2])
            cur = alias.get(new, new)
            if code == b"R":
                alias[old] = cur
            files.add(cur)
            i += 3
        else:
            if i + 1 >= len(toks):
                break
            path = _text(toks[i + 1])
            files.add(alias.get(path, path))
            i += 2
    if author is not None:
        commits.append(Commit(author, frozenset(files)))
    return commits


def summarize(commits: list[Commit], head: str | None = None) -> History:
    churn: dict[str, int] = {}
    people: dict[str, set[str]] = {}
    shared: dict[tuple[str, str], int] = {}
    bulk = 0
    for c in commits:
        for f in c.files:
            churn[f] = churn.get(f, 0) + 1
            people.setdefault(f, set()).add(c.author.lower())
        if len(c.files) > MAX_CHANGESET:
            bulk += 1
            continue
        for pair in combinations(sorted(c.files), 2):
            shared[pair] = shared.get(pair, 0) + 1
    return History(
        head,
        len(commits),
        bulk,
        churn,
        {f: len(who) for f, who in people.items()},
        {p: n for p, n in shared.items() if n >= MIN_SHARED},
    )


def to_json(h: History) -> dict:
    return {
        "head": h.head,
        "commits": h.commits,
        "bulk": h.bulk,
        "churn": h.churn,
        "authors": h.authors,
        "pairs": [[a, b, n] for (a, b), n in h.pairs.items()],
    }


def _counts(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise TypeError("not a mapping")
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, int) or isinstance(v, bool):
            raise TypeError("not a count")
        out[k] = v
    return out


def from_json(data: object) -> History | None:
    """The cached summary, or None for anything malformed."""
    try:
        if not isinstance(data, dict):
            return None
        head = data["head"]
        if head is not None and not isinstance(head, str):
            return None
        pairs = {}
        for a, b, n in data["pairs"]:
            if not (isinstance(a, str) and isinstance(b, str) and type(n) is int):
                return None
            pairs[(a, b)] = n
        commits, bulk = data["commits"], data["bulk"]
        if type(commits) is not int or type(bulk) is not int:
            return None
        return History(head, commits, bulk, _counts(data["churn"]), _counts(data["authors"]), pairs)
    except (KeyError, TypeError, ValueError):
        return None


def _log(root: Path) -> bytes:
    """The last ``WINDOW`` non-merge commits. Every option the repo's config could
    reshape is pinned on the command line: format, renames, signatures."""
    proc = scan.git(
        root,
        "log",
        "--no-merges",
        "--no-show-signature",
        "--no-color",
        "-M",
        "--name-status",
        "-z",
        _FORMAT,
        "-n",
        str(WINDOW),
        "--",
        timeout=GIT_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise scan.XrayError(proc.stderr.decode(errors="replace").strip() or "git log failed")
    return proc.stdout


def _cache_path(root: Path, state_dir: Path) -> Path:
    return state_dir / f"xray-history-{hook_common._digest(root)}.json"


def _failed_path(root: Path, state_dir: Path) -> Path:
    return state_dir / f"xray-history-failed-{hook_common._digest(root)}"


def read(root: Path, state_dir: Path, *, retry: bool = True) -> History:
    """The checkout's history, cached by HEAD. An unborn branch has none.

    A log that failed (a timeout) is remembered for that HEAD; with ``retry``
    off - the read hook, which runs on every first read of a file - it is not
    run again until HEAD moves, so one slow history costs one timeout, not one
    per file. The tool keeps ``retry`` on: an agent that asks gets a fresh try."""
    head = scan.commit(root)
    if head is None:
        return EMPTY
    cache = _cache_path(root, state_dir)
    cached = from_json(hook_common._read_json(cache))
    if cached is not None and cached.head == head:
        return cached
    failed = _failed_path(root, state_dir)
    if not retry and hook_common._read_json(failed) == head:
        raise scan.XrayError("git log failed for this HEAD; not retried in a hook")
    try:
        h = summarize(parse(_log(root)), head=head)
    except scan.XrayError:
        hook_common._write_json(failed, head)
        raise
    hook_common._write_json(cache, to_json(h))
    failed.unlink(missing_ok=True)
    return h
