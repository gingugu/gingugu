"""Extract a repo's import graph from its git working tree, in every language we parse.

Runs on the client, because it reads the caller's disk: the brain never sees a
path, only the :meth:`Extraction.payload`. Nothing here imports or executes the
repo being analysed - files are read as bytes, once, and each language adapter
(``lang/``) parses its share.

**Why the fingerprint hashes content, not git state.** ``git status`` reads
" M" for a file whether it was edited once or ten times, and an empty commit
moves HEAD without changing a line of code. Neither is a usable cache key. The
fingerprint is a hash of every source file's bytes, every manifest an adapter
reads (``pyproject.toml``, ``go.mod``, ``tsconfig.json``...) and the extractor
version, so it changes exactly when the answer could, and bumping
``EXTRACTOR_VERSION`` invalidates graphs built by older logic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from . import lang, validate
from . import parse_cache as _pc
from .model import Edge, FileInfo, LangResult
from .scan import XrayError as XrayError
from .scan import commit, is_manifest, read_sources, toplevel
from .symbols import Symbols

__all__ = ["EXTRACTOR_VERSION", "Edge", "Extraction", "FileInfo", "XrayError", "extract"]

# 2: every language in ``lang/``, nodes carry lang + kind, package nodes.
# 3: the Python parse also carries symbol records (X17).
EXTRACTOR_VERSION: int = 3
MAX_FILE_BYTES: int = 2_000_000  # real source is far smaller; read at call time
MAX_TOTAL_BYTES: int = 256_000_000  # vite's sources are ~10MB; read at call time


@dataclass
class Extraction:
    root: str
    commit: str | None
    fingerprint: str
    files: list[FileInfo] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    unresolved: int = 0
    dynamic: int = 0
    external: int = 0
    parse_errors: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    symbols: Symbols = field(default_factory=Symbols)  # who calls what; never in ``payload``

    def payload(self) -> dict:
        """JSON-safe wire form. Omits ``root``: a client path never leaves the client."""
        return {
            "fingerprint": self.fingerprint,
            "extractor_version": EXTRACTOR_VERSION,
            "commit": self.commit,
            "files": [
                {
                    "path": f.path,
                    "is_test": f.is_test,
                    "is_entry": f.is_entry,
                    "lang": f.lang,
                    "kind": f.kind,
                }
                for f in self.files
            ],
            "edges": [
                {
                    "src": e.src,
                    "dst": e.dst,
                    "kind": e.kind,
                    "certain": e.certain,
                    "implicit": e.implicit,
                }
                for e in self.edges
            ],
            "unresolved": self.unresolved,
            "dynamic": self.dynamic,
            "external": self.external,
            "parse_errors": list(self.parse_errors),
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped],
        }


def _scan(root: Path):
    return read_sources(root, MAX_FILE_BYTES, lang.extensions(), lang.manifests(), MAX_TOTAL_BYTES)


def _fingerprint(
    sources: dict[str, bytes], manifests: dict[str, bytes], skipped: list[tuple[str, str]]
) -> str:
    h = hashlib.sha256(f"xray-v{EXTRACTOR_VERSION}\n".encode())
    for path in sorted(sources):
        h.update(f"{path}\0{hashlib.sha256(sources[path]).hexdigest()}\n".encode())
    for path in sorted(manifests):
        h.update(f"manifest\0{path}\0{hashlib.sha256(manifests[path]).hexdigest()}\n".encode())
    for path, reason in sorted(skipped):
        h.update(f"skip\0{path}\0{reason}\n".encode())
    return h.hexdigest()


def fingerprint(path: str | Path) -> str:
    """Cheap cache key for a repo: reads and hashes bytes, parses nothing."""
    return _fingerprint(*_scan(toplevel(path)))


def _raws(adapter, mine: dict[str, bytes], cached: dict[str, object]):
    """(file -> raw, slot -> raw) for one adapter, parsing only what ``cached`` lacks
    or fails to validate: a cache entry is any JSON until proven otherwise."""
    raws: dict[str, object] = {}
    kept: dict[str, object] = {}
    for rel, data in mine.items():
        slot = _pc.slot(max((e for e in adapter.extensions if rel.endswith(e)), key=len), data)
        raw = cached.get(slot)
        if slot not in cached or not adapter.valid(raw):
            raw = adapter.parse(rel, data)
        raws[rel] = kept[slot] = raw
    return raws, kept


def extract(path: str | Path, parse_cache: str | Path | None = None) -> Extraction:
    """Build the import graph for the git repo containing ``path``.

    With ``parse_cache``, each file's parse is cached there by content, so only
    bytes not seen before are parsed. Without it nothing is written anywhere."""
    root = toplevel(path)
    sources, manifests, skipped = _scan(root)
    out = Extraction(
        root=str(root),
        commit=commit(root),
        fingerprint=_fingerprint(sources, manifests, skipped),
    )
    seen: set[str] = set()
    before = _pc.load(parse_cache, EXTRACTOR_VERSION) if parse_cache else {}
    after: _pc.Raws = {}
    for adapter in lang.ADAPTERS:
        mine = {p: b for p, b in sources.items() if lang.for_path(p) is adapter}
        wanted = {p: b for p, b in manifests.items() if is_manifest(p, adapter.manifests)}
        if not mine:
            continue
        try:
            if parse_cache:
                raws, after[adapter.name] = _raws(adapter, mine, before.get(adapter.name, {}))
                res = adapter.link(raws, wanted)
            else:
                res = adapter.extract(mine, wanted)
        except RecursionError:
            after.pop(adapter.name, None)
            # Hostile input past a bound an adapter missed: that language's files
            # stay nodes, reported unparsed, and every other language still lands.
            res = LangResult(
                files=[FileInfo(p, False, False, adapter.name) for p in mine],
                parse_errors=sorted(mine),
            )
        for f in res.files:
            # A synthetic node (a package) never shadows a real file, and a name
            # the brain would refuse is never shipped: it sinks the whole payload.
            if f.path not in seen and validate.is_valid_path(f.path):
                seen.add(f.path)
                out.files.append(f)
        out.edges.extend(res.edges)
        out.unresolved += res.unresolved
        out.dynamic += res.dynamic
        out.external += res.external
        out.parse_errors.extend(res.parse_errors)
        if res.symbols.defs:  # only Python produces one
            out.symbols = res.symbols
        skipped.extend(res.skipped)

    # The brain refuses a payload whole over one dangling or repeated edge, so an
    # adapter's mistake is contained here: such an edge is counted, not shipped.
    edges: dict[tuple[str, str], Edge] = {}
    for e in out.edges:
        if e.src not in seen or e.dst not in seen or e.src == e.dst:
            out.unresolved += e.src != e.dst
            continue
        edges.setdefault((e.src, e.dst), e)
    out.files.sort(key=lambda f: f.path)
    out.edges = sorted(edges.values(), key=lambda e: (e.src, e.dst))
    out.parse_errors.sort()
    out.skipped = sorted(set(skipped))
    if parse_cache and after != before:  # also prunes: only sources present now are kept
        _pc.save(parse_cache, EXTRACTOR_VERSION, after)
    return out
