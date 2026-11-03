"""X23 mentions: the shared collector and line helpers."""

from __future__ import annotations

import re

from .model import Fact, norm_repo

MAX_LINE = 4000


def split_lines(text: str) -> list[str]:
    """Lines of ``text`` (index = line number - 1); a line too long to scan
    safely becomes empty so line numbers stay true."""
    return [ln if len(ln) <= MAX_LINE else "" for ln in text.replace("\r", "").split("\n")]


class Sink:
    """Collects facts, one per (kind, key, path, target), at its first line."""

    def __init__(self) -> None:
        self._facts: dict[tuple[str, str, str, str], Fact] = {}
        self.names = 0

    def add(
        self,
        kind: str,
        key: str | None,
        path: str,
        line: int,
        detail: str = "",
        target: str = "",
    ) -> bool:
        if not key:
            return False
        ident = (kind, key, path, target)
        old = self._facts.get(ident)
        if old is not None and old.line <= line:
            return False
        self._facts[ident] = Fact(kind, key, path, line, detail, target)
        return old is None

    def result(self) -> list[Fact]:
        return sorted(self._facts.values(), key=lambda f: (f.path, f.line, f.kind, f.key, f.target))


def repo_from_url(url: str) -> str | None:
    """``owner/name`` from the last two path parts of a git URL (https, ssh,
    scp-style), or None when the URL is templated or has no such parts."""
    url = url.strip().strip("\"'")
    if "$" in url or "{{" in url:
        return None
    url = re.split(r"[?#]", url, maxsplit=1)[0]
    if "://" in url:
        segs = url.split("://", 1)[1].split("/")[1:]
    elif ":" in url:
        segs = url.split(":", 1)[1].split("/")
    else:
        segs = url.split("/")
    segs = [s for s in segs if s]
    if len(segs) < 2:
        return None
    return norm_repo("/".join(segs[-2:]))


def unquote(value: str) -> str:
    return value.strip().strip("\"'")
