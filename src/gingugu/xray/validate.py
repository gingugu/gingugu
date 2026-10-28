"""Validating an X-ray payload as data, before the brain writes any of it.

A payload comes from a client and may come from any token holding write on the
namespace. Paths in it are names inside a snapshot, never opened, so each must
be relative and plain; every edge must join files the payload lists; nothing
may repeat; every count is a bounded count. A bad payload is refused whole.

**The caps are a CPU bound, not a storage one.** Rank is pure-Python PageRank at
ingest, on the brain's one connection, so an oversized graph stalls every other
client for its duration. 50,000 files is several times any repository this is
for; a larger one is refused rather than allowed to hold the brain.

``graph_digest`` hashes the validated graph. The fingerprint is the client's
claim about its checkout and cannot be checked here; the digest is what the
brain can check, and what lets a refresh tell a different graph from the same.
"""

from __future__ import annotations

import hashlib
import json
import re

# ``package``: a file leaning on a package node; ``member``: a package node
# reaching one of its files. Both only appear where a language imports packages.
KINDS = ("module", "conditional", "lazy", "type_checking", "package", "member")
NODE_KINDS = ("file", "package")
REASONS = ("symlink", "outside", "unreadable", "too_large", "malformed")
# Sources left out on purpose: they never make a graph incomplete.
DELIBERATE = ("symlink",)
MAX_FILES = 50_000
MAX_EDGES = 500_000
MAX_COUNT = 2**31 - 1
_MAX_PATH = 1024
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{4,64}$")
_COUNTS = ("unresolved", "dynamic", "external")
# A name, not a list: a client that learns a language must not need a new brain.
_LANG = re.compile(r"^[a-z][a-z0-9_+-]{0,31}$")


class XrayStoreError(ValueError):
    pass


def _path(value) -> str:
    ok = (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_PATH
        and not value.startswith("/")
        and "\\" not in value
        and "\x00" not in value
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )
    if not ok:
        raise XrayStoreError(f"invalid path {value!r}: must be relative, '/'-separated, no '..'")
    return value


def _list(payload: dict, key: str, cap: int) -> list:
    value = payload.get(key, [])
    if not isinstance(value, list) or len(value) > cap:
        raise XrayStoreError(f"{key} must be a list of at most {cap}")
    return value


def _count(payload: dict, key: str) -> int:
    n = payload.get(key)
    if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_COUNT:
        raise XrayStoreError(f"{key} must be an integer from 0 to {MAX_COUNT}")
    return n


def _files(payload: dict) -> dict[str, tuple[bool, bool, str, str]]:
    """path -> (is_test, is_entry, lang, kind). ``lang``/``kind`` default to what
    every payload before them was - Python files - so an older client still ingests."""
    files: dict[str, tuple[bool, bool, str, str]] = {}
    for f in _list(payload, "files", MAX_FILES):
        if not isinstance(f, dict) or not all(
            isinstance(f.get(k), bool) for k in ("is_test", "is_entry")
        ):
            raise XrayStoreError("each file needs path, is_test and is_entry")
        path = _path(f.get("path"))
        if path in files:
            raise XrayStoreError(f"file {path} is listed twice")
        lang, kind = f.get("lang", "python"), f.get("kind", "file")
        if not (isinstance(lang, str) and _LANG.fullmatch(lang)):
            raise XrayStoreError(f"file {path}: lang must be a short lowercase name")
        if kind not in NODE_KINDS:
            raise XrayStoreError(f"file {path}: kind must be one of {NODE_KINDS}")
        if kind == "package" and (f["is_test"] or f["is_entry"]):
            raise XrayStoreError(f"package {path} cannot be a test or an entry point")
        files[path] = (f["is_test"], f["is_entry"], lang, kind)
    return files


def _edges(payload: dict, files) -> dict[tuple[str, str], tuple[str, bool, bool]]:
    edges: dict[tuple[str, str], tuple[str, bool, bool]] = {}
    for e in _list(payload, "edges", MAX_EDGES):
        if not isinstance(e, dict):
            raise XrayStoreError("each edge must be an object")
        src, dst = _path(e.get("src")), _path(e.get("dst"))
        if src not in files or dst not in files:
            raise XrayStoreError(f"edge {src} -> {dst} names a file the payload does not list")
        flags = (e.get("certain"), e.get("implicit"))
        if e.get("kind") not in KINDS or not all(isinstance(f, bool) for f in flags):
            raise XrayStoreError(
                f"edge {src} -> {dst}: kind must be one of {KINDS}, certain/implicit bools"
            )
        if (src, dst) in edges:
            raise XrayStoreError(f"edge {src} -> {dst} is listed twice")
        if src != dst:
            edges[(src, dst)] = (e["kind"], e["certain"], e["implicit"])
    return edges


def _skipped(payload: dict) -> list[dict]:
    out = []
    for s in _list(payload, "skipped", MAX_FILES):
        if not isinstance(s, dict) or s.get("reason") not in REASONS:
            raise XrayStoreError(f"each skip needs path and a reason in {REASONS}")
        out.append({"path": _path(s.get("path")), "reason": s["reason"]})
    return sorted(out, key=lambda s: (s["path"], s["reason"]))


def payload(body) -> dict:
    """The validated, normalized payload, with its ``graph_digest``."""
    if not isinstance(body, dict):
        raise XrayStoreError("payload must be an object")
    fp = body.get("fingerprint")
    if not isinstance(fp, str) or not _FINGERPRINT.fullmatch(fp):
        raise XrayStoreError("fingerprint must be 64 lowercase hex characters")
    version = body.get("extractor_version")
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= MAX_COUNT:
        raise XrayStoreError("extractor_version must be a positive integer")
    commit = body.get("commit")
    if commit is not None and not (isinstance(commit, str) and _COMMIT.fullmatch(commit)):
        raise XrayStoreError("commit must be a hex sha or null")
    files = _files(body)
    parse_errors = sorted({_path(p) for p in _list(body, "parse_errors", MAX_FILES)})
    if any(p not in files for p in parse_errors):
        raise XrayStoreError("parse_errors names a file the payload does not list")
    v = {
        "fingerprint": fp,
        "extractor_version": version,
        "commit": commit,
        "files": files,
        "edges": _edges(body, files),
        "parse_errors": parse_errors,
        "skipped": _skipped(body),
        **{key: _count(body, key) for key in _COUNTS},
    }
    graph = {
        "files": sorted([p, *flags] for p, flags in files.items()),
        "edges": sorted([*pair, *flags] for pair, flags in v["edges"].items()),
        **{k: v[k] for k in ("extractor_version", "parse_errors", "skipped", *_COUNTS)},
    }
    v["graph_digest"] = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    return v
