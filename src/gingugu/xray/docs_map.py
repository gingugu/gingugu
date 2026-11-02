"""X22: the docs map - which docs cite a file, in this repo and in sibling checkouts.

A runbook names the scripts and configs it describes. X3's matcher already
finds a path in prose and pins it to one repo file; pointed at this repo's
docs, and at the docs of the other checkouts on this machine, it answers
"which docs go stale when this file changes". A bare path in a sibling's doc
means the sibling's own file, so a sibling cites this repo only by a
repo-qualified path (``repo/lib/x.py``) or a GitHub link to this repo.

Client side only: the brain is never asked. The cache is a hint, not a truth:
it is read as untrusted input and rebuilt whole on anything malformed.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from . import hook_common, repo_id, scan
from .docs_cites import Cite, DocsMap, cited, citing, citing_gone  # noqa: F401
from .scar_query import _TOKEN, MAX_TOKEN_CHARS

DOC_EXTS = (".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc")
MAX_DOCS = 5000  # per repo
MAX_DOC_BYTES = 262_144  # read at most this much of one doc
MAX_SIBLINGS = 64
MAX_TOKENS = 1000  # per doc
GIT_TIMEOUT_S = 10

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
    out: list[str] = []
    for match in _GITHUB.finditer(text):
        if f"{match.group(1)}/{match.group(2)}".lower() != ident.lower():
            continue
        path = re.split(r"[#?]", match.group(3), maxsplit=1)[0].rstrip(_TRAIL)
        if path and path not in out:
            out.append(path)
    return out


def qualified(text: str, ident: str | None) -> list[str]:
    """What ``text`` says about the repo ``ident``: paths prefixed with its name
    (``widget/lib/x.py``) and GitHub links to it. Unique, first seen first."""
    if not ident:
        return []
    name = ident.rpartition("/")[2].lower()
    out: list[str] = []
    for token in tokens(text):
        head, _, rest = token.partition("/")
        if rest and head.lower() == name and rest not in out:
            out.append(rest)
    out += [p for p in github_paths(text, ident) if p not in out]
    return out


def _read_text(path: Path) -> str:
    """The one I/O seam for reading a doc."""
    with open(path, "rb") as handle:
        return handle.read(MAX_DOC_BYTES).decode("utf-8", errors="replace")


# --- the cache ---------------------------------------------------------------


def _strs(value: object) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def _load(root: Path, state_dir: Path) -> tuple[object, dict, dict, object]:
    """(ident, local, siblings, raw) from the cache, every field type-checked;
    anything off drops that part, so a bad cache costs a rebuild and no more."""
    raw = hook_common._read_json(state_dir / f"xray-docs-{hook_common._digest(root)}.json")
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
    if isinstance(raw.get("siblings"), dict):
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


def _plain_file(path: Path) -> os.stat_result | None:
    """The stat of a regular file that is not a link; None for anything else."""
    try:
        st = path.lstat()
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def _local(root: Path, docs: list[str], ident: str, old: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for doc in docs:
        st = _plain_file(root / doc)
        if st is None:
            continue
        prev = old.get(doc)
        if prev and prev["size"] == st.st_size and prev["mtime_ns"] == st.st_mtime_ns:
            out[doc] = prev
            continue
        try:
            text = _read_text(root / doc)
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


def _sibling(child: Path, own: str) -> tuple[str | None, dict[str, list[str]]]:
    proc = scan.git(child, "ls-files", "-z", "--cached", timeout=GIT_TIMEOUT_S)
    if proc.returncode != 0:
        raise scan.XrayError("git ls-files failed")
    refs: dict[str, list[str]] = {}
    for doc in _docs(_names(proc)):
        if _plain_file(child / doc) is None:
            continue
        try:
            found = qualified(_read_text(child / doc), own)
        except OSError:
            continue
        if found:
            refs[doc] = found
    return scan.commit(child), refs


def _siblings(root: Path, own: str, old: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """(by sibling path, for the cache; by ident, for the map)."""
    cached: dict[str, dict] = {}
    by_ident: dict[str, dict] = {}
    if not own:
        return cached, by_ident
    for child in _candidates(root):
        where = str(child)
        try:
            prev = old.get(where)
            head = scan.commit(child)
            if prev and head is not None and prev["head"] == head:
                entry = prev
            else:
                ident = repo_id.of(child) or child.name
                refs = {} if ident == own else _sibling(child, own)[1]
                entry = {"ident": ident, "head": head, "refs": refs}
        except (scan.XrayError, OSError):
            continue
        if entry["ident"] == own:
            continue  # this repo under another path: a worktree or a second clone
        cached[where] = entry
        by_ident.setdefault(entry["ident"], entry["refs"])
    return cached, by_ident


def read(root: Path, state_dir: Path) -> DocsMap:
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
    cached, by_ident = _siblings(root, own, old_sibs)
    fresh = {"ident": own, "local": local, "siblings": cached}
    if fresh != raw:
        hook_common._write_json(state_dir / f"xray-docs-{hook_common._digest(root)}.json", fresh)
    return DocsMap(frozenset(names), {d: e["tokens"] for d, e in local.items()}, by_ident)
