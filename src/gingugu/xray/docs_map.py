"""X22: the docs map - which docs cite a file, in this repo and in sibling checkouts.

A runbook names the scripts and configs it describes. X3's matcher already
finds a path in prose and pins it to one repo file; pointed at this repo's
docs, and at the docs of the other checkouts on this machine, it answers
"which docs go stale when this file changes". A bare path in a sibling's doc
means the sibling's own file, so a sibling cites this repo only by a
repo-qualified path (``repo/lib/x.py``) or a GitHub link to this repo.

Client side only: the brain is never asked. The cache is a hint, not a truth:
it is read as untrusted input, and a malformed entry is dropped and re-read.
"""

from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path, PurePosixPath

from . import hook_common, repo_id, scan
from .docs_cites import Cite, DocsMap, cited, citing, citing_gone  # noqa: F401
from .scar_query import _TOKEN, MAX_TOKEN_CHARS

DOC_EXTS = (".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc")
MAX_DOCS = 5000  # per repo
MAX_DOC_BYTES = 262_144  # read at most this much of one doc
MAX_SIBLINGS = 64
MAX_TOKENS = 1000  # per doc, path tokens and GitHub links each
GIT_TIMEOUT_S = 10
# Siblings are other people's checkouts and the brief can run inside a hook: past
# this, a sibling with no cached entry is skipped for this read, not waited on.
SIBLING_BUDGET_S = 3.0
MAX_CACHE_BYTES = 16 * 1024 * 1024
# Bumped whenever ``qualified`` reads more: a sibling's refs are cached by its HEAD,
# so without this a sibling that never moves would keep the old reading forever.
MATCH_VERSION = 2

_GITHUB = re.compile(
    r"github\.com/([\w.-]+)/([\w.-]+)/(?:blob|tree|raw)/[^/\s]+/(\S+)", re.IGNORECASE
)
_TRAIL = ")]}>.,;:!\"'`"


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_DOCS", "on").lower() not in ("0", "off", "false")


def tokens(text: str) -> list[str]:
    """Unique path tokens, first seen first."""
    out: list[str] = []
    for match in _TOKEN.finditer(text):
        token = match.group(1)
        if len(token) <= MAX_TOKEN_CHARS and token not in out:
            out.append(token)
            if len(out) == MAX_TOKENS:
                break
    return out


def github_paths(text: str, ident: str | None) -> list[str]:
    """Paths of GitHub links into the repo ``owner/name`` that ``ident`` names."""
    if not ident or "/" not in ident:
        return []
    out: dict[str, None] = {}  # ordered and O(1) to test: a doc can hold thousands
    for match in _GITHUB.finditer(text):
        if f"{match.group(1)}/{match.group(2)}".lower() != ident.lower():
            continue
        path = re.split(r"[#?]", match.group(3), maxsplit=1)[0].rstrip(_TRAIL)
        if path and len(path) <= MAX_TOKEN_CHARS:
            out[path] = None
            if len(out) == MAX_TOKENS:
                break
    return list(out)


def qualified(text: str, ident: str | None) -> list[str]:
    """What ``text`` says about the repo ``ident``: paths prefixed with its name
    (``widget/lib/x.py``) or its ``owner/name``, GitHub links to it, and prose that
    places a path in it ("lives in acme/widget, as lib/x.py" - X23's reader).
    Unique, first seen first."""
    if not ident:
        return []
    from .links.prose import doc_refs

    name = ident.rpartition("/")[2].lower()
    out: list[str] = []
    for token in tokens(text):
        head, _, rest = token.partition("/")
        if rest and head.lower() == name and rest not in out:
            out.append(rest)
    out += [p for p in github_paths(text, ident) if p not in out]
    whole = ident.lower()
    for key, path in doc_refs(text):
        if (key == whole or ("/" not in key and key == name)) and path not in out:
            out.append(path)
    return out


_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)


def _read_text(path: Path) -> str:
    """The one I/O seam for reading a doc (and, for X23, any checkout file).
    ``_inside`` checked the name; a checkout can still swap the file for a link or
    a FIFO before this open, so the open itself refuses a link and never blocks,
    and what it opened must be a regular file."""
    if not hasattr(os, "O_NOFOLLOW") and os.path.islink(path):
        raise OSError(f"a link: {path}")  # Windows has no O_NOFOLLOW: check, then open
    fd = os.open(path, _OPEN_FLAGS)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(f"not a regular file: {path}")
        return handle.read(MAX_DOC_BYTES).decode("utf-8", errors="replace")


# --- the cache ---------------------------------------------------------------


def _strs(value: object) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def _load(root: Path, state_dir: Path) -> tuple[object, dict, dict, object]:
    """(ident, local, siblings, raw) from the cache, every field type-checked;
    anything off drops that part, so a bad cache costs a rebuild and no more."""
    path = state_dir / f"xray-docs-{hook_common._digest(root)}.json"
    try:
        too_big = path.stat().st_size > MAX_CACHE_BYTES
    except OSError:
        too_big = False
    raw = None if too_big else hook_common._read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("ident"), str):
        return None, {}, {}, raw
    local: dict[str, dict] = {}
    if isinstance(raw.get("local"), dict):
        for doc, e in raw["local"].items():
            toks = _strs(e.get("tokens")) if isinstance(e, dict) else None
            if toks is None or not all(type(e.get(k)) is int for k in ("size", "mtime_ns")):
                continue
            local[doc] = {"size": e["size"], "mtime_ns": e["mtime_ns"], "tokens": toks}
    sibs: dict[str, dict] = {}
    if isinstance(raw.get("siblings"), dict) and raw.get("v") == MATCH_VERSION:
        for where, e in raw["siblings"].items():
            if not isinstance(e, dict) or not isinstance(e.get("ident"), str):
                continue
            if not (e.get("head") is None or isinstance(e["head"], str)):
                continue
            refs = e.get("refs")
            if not isinstance(refs, dict):
                continue
            good = {d: _strs(v) for d, v in refs.items()}
            if any(v is None for v in good.values()):
                continue
            sibs[where] = {"ident": e["ident"], "head": e.get("head"), "refs": good}
    return raw["ident"], local, sibs, raw


# --- reading -----------------------------------------------------------------


def _names(proc) -> list[str]:
    return sorted(
        {p.decode("utf-8", errors="surrogateescape") for p in proc.stdout.split(b"\0") if p}
    )


def _docs(names: list[str]) -> list[str]:
    return [n for n in names if n.lower().endswith(DOC_EXTS)][:MAX_DOCS]


def _inside(root: Path, name: str) -> Path | None:
    """``root / name`` when it is a regular file inside ``root``: no absolute name,
    no ``..``, no link at the end or on the way (a crafted index can list any name)."""
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or os.path.isabs(name) or ".." in pure.parts:
        return None
    full = root / name
    try:
        if not stat.S_ISREG(full.lstat().st_mode):
            return None
        if not full.resolve().is_relative_to(root.resolve()):
            return None
    except (OSError, RuntimeError):
        return None
    return full


def _local(root: Path, docs: list[str], ident: str, old: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for doc in docs:
        full = _inside(root, doc)
        if full is None:
            continue
        try:
            st = full.stat()
        except OSError:
            continue
        prev = old.get(doc)
        if prev and prev["size"] == st.st_size and prev["mtime_ns"] == st.st_mtime_ns:
            out[doc] = prev
            continue
        try:
            text = _read_text(full)
        except OSError:
            continue
        toks = tokens(text)
        toks += [p for p in github_paths(text, ident) if p not in toks]
        out[doc] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "tokens": toks}
    return out


def _candidates(root: Path) -> list[Path]:
    """Checkouts next to this one: each search root's direct children, in order."""
    me = os.path.realpath(root)
    found: list[Path] = []
    for base in repo_id.roots():
        try:
            children = sorted(base.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_dir() or not (child / ".git").exists():
                    continue
            except OSError:
                continue
            if os.path.realpath(child) != me:
                found.append(child)
            if len(found) >= MAX_SIBLINGS:
                return found
    return found


def _sibling(child: Path, own: str, timeout: float) -> dict[str, list[str]]:
    proc = scan.git(child, "ls-files", "-z", "--cached", timeout=timeout)
    if proc.returncode != 0:
        raise scan.XrayError("git ls-files failed")
    refs: dict[str, list[str]] = {}
    for doc in _docs(_names(proc)):
        full = _inside(child, doc)
        if full is None:
            continue
        try:
            found = qualified(_read_text(full), own)
        except OSError:
            continue
        if found:
            refs[doc] = found
    return refs


def _siblings(
    root: Path, own: str, old: dict, budget_s: float | None = None
) -> tuple[dict[str, dict], dict[str, dict]]:
    """(by sibling path, for the cache; by ident, for the map). Past the time
    budget a sibling keeps its cached entry unchecked, or is skipped."""
    cached: dict[str, dict] = {}
    by_ident: dict[str, dict] = {}
    if not own:
        return cached, by_ident
    deadline = time.monotonic() + (SIBLING_BUDGET_S if budget_s is None else budget_s)
    for child in _candidates(root):
        where = str(child)
        prev = old.get(where)
        left = deadline - time.monotonic()
        try:
            if left <= 0:
                if prev is None:
                    continue
                entry = prev
            else:
                head = scan.commit(child)
                if prev and head is not None and prev["head"] == head:
                    entry = prev
                else:
                    ident = repo_id.of(child) or child.name
                    keep = repo_id.valid(ident) and ident != own
                    refs = _sibling(child, own, min(left, GIT_TIMEOUT_S)) if keep else {}
                    entry = {"ident": ident, "head": head, "refs": refs}
        except (scan.XrayError, OSError):
            continue
        if entry["ident"] == own or not repo_id.valid(entry["ident"]):
            continue  # this repo under another path (a worktree, a clone), or a bad name
        cached[where] = entry
        by_ident.setdefault(entry["ident"], entry["refs"])
    return cached, by_ident


def read(root: Path, state_dir: Path, *, budget_s: float | None = None) -> DocsMap:
    """The docs map for the checkout at ``root``, from the cache where it holds."""
    proc = scan.git(
        root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", timeout=GIT_TIMEOUT_S
    )
    if proc.returncode != 0:
        raise scan.XrayError("git ls-files failed")
    names = _names(proc)
    own = repo_id.of(root) or ""
    ident, old_local, old_sibs, raw = _load(root, state_dir)
    if ident != own:
        old_local, old_sibs = {}, {}  # another identity reads every doc differently
    local = _local(root, _docs(names), own, old_local)
    cached, by_ident = _siblings(root, own, old_sibs, budget_s)
    fresh = {"v": MATCH_VERSION, "ident": own, "local": local, "siblings": cached}
    if fresh != raw:
        hook_common._write_json(state_dir / f"xray-docs-{hook_common._digest(root)}.json", fresh)
    return DocsMap(frozenset(names), {d: e["tokens"] for d, e in local.items()}, by_ident)
