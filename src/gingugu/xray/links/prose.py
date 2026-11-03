"""X23 mentions: which repo files a doc names, read from prose."""

from __future__ import annotations

import re
from collections.abc import Iterator

from .mentions_sink import split_lines
from .model import norm_repo

_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/){2,}[\w.-]+)")
_LINK = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/(?:blob|tree|raw)/[^/\s]+/([^\s#?)>\]\"'`]+)")
_PROSE = re.compile(
    r"(?<![\w./-])([\w.-]+/[\w.-]+)`?(?:,?\s+(?:as|at|in|under)\s+|:\s*)`?([\w.-]+(?:/[\w.-]+)*)"
)
_EXT = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
_TRAIL = ".,;:)"


def _pair(repo: str, path: str) -> tuple[str, str] | None:
    key = norm_repo(repo)
    path = path.rstrip(_TRAIL)
    return (key, path) if key and path else None


def _line_refs(line: str) -> Iterator[tuple[str, str]]:
    for m in _PATH.finditer(line):
        tok = m.group(1).rstrip(_TRAIL)
        if not _EXT.search(tok):
            continue
        segs = tok.split("/")
        for repo, rest in (("/".join(segs[:2]), segs[2:]), (segs[0], segs[1:])):
            pair = _pair(repo, "/".join(rest))
            if pair:
                yield pair
    for m in _LINK.finditer(line):
        pair = _pair(f"{m.group(1)}/{m.group(2)}", m.group(3))
        if pair:
            yield pair
    for m in _PROSE.finditer(line):
        if _EXT.search(m.group(2).rstrip(_TRAIL)):
            pair = _pair(m.group(1), m.group(2))
            if pair:
                yield pair


def iter_refs(lines: list[str]) -> Iterator[tuple[int, str, str]]:
    """(line number, repo key, path) for every ref in pre-split ``lines``."""
    for i, ln in enumerate(lines):
        if ln and "/" in ln:
            for repo, path in _line_refs(ln):
                yield i + 1, repo, path


def doc_refs(text: str) -> list[tuple[str, str]]:
    """``(repo key, path)`` pairs a doc names, unique, in first-seen order."""
    if not isinstance(text, str):
        return []
    seen: dict[tuple[str, str], None] = {}
    for _, repo, path in iter_refs(split_lines(text)):
        seen.setdefault((repo, path), None)
    return list(seen)
