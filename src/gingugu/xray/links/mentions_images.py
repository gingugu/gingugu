"""X23 mentions: container images a repo deploys or builds from."""

from __future__ import annotations

import re

from .mentions_sink import Sink, unquote
from .model import IMAGE, norm_image

_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
_PLAIN = re.compile(r"^\s*(?:-\s+)?[\"']?image[\"']?:\s*([^\s#\"'][^\s#]*)")
_QUOTED = re.compile(
    r"(?<![\w.-])[\"']?image[\"']?\s*[:=]\s*(?:\"([^\"\n]{1,300})\"|'([^'\n]{1,300})')"
)
_BLOCK = re.compile(r"^(\s*)(?:-\s+)?image:\s*(?:#.*)?$")
_REPOSITORY = re.compile(r"^\s*repository:\s*(\S+)")


def scan_dockerfile(path: str, lines: list[str], sink: Sink) -> None:
    stages: set[str] = set()
    for i, ln in enumerate(lines):
        m = _FROM.match(ln)
        if not m:
            continue
        ref = m.group(1)
        key = norm_image(ref)
        if not (key and "/" not in key and key in stages):
            sink.add(IMAGE, key, path, i + 1)
        if m.group(2):
            stages.add(m.group(2).lower())


def scan_images(path: str, lines: list[str], sink: Sink) -> None:
    block_indent: int | None = None
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        if block_indent is not None:
            if len(ln) - len(ln.lstrip()) > block_indent:
                m = _REPOSITORY.match(ln)
                if m:
                    sink.add(IMAGE, norm_image(unquote(m.group(1))), path, i + 1)
                continue
            block_indent = None
        if "image" not in ln:
            continue
        b = _BLOCK.match(ln)
        if b:
            block_indent = len(b.group(1))
            continue
        for q in _QUOTED.finditer(ln):
            sink.add(IMAGE, norm_image(q.group(1) or q.group(2)), path, i + 1)
        p = _PLAIN.match(ln)
        if p:
            sink.add(IMAGE, norm_image(p.group(1)), path, i + 1)
