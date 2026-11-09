"""X22: what a doc names - path tokens, GitHub links, and repo-qualified paths.

Pure text in, paths out: no file is read here. Split from ``docs_map``, which
reads the docs these run over.
"""

from __future__ import annotations

import re

from .scar_query import _TOKEN, MAX_TOKEN_CHARS

MAX_TOKENS = 1000  # per doc, path tokens and GitHub links each

_GITHUB = re.compile(
    r"github\.com/([\w.-]+)/([\w.-]+)/(?:blob|tree|raw)/[^/\s]+/(\S+)", re.IGNORECASE
)
_TRAIL = ")]}>.,;:!\"'`"


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
