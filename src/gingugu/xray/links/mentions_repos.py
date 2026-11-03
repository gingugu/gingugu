"""X23 mentions: a sibling repo named outright - by name, as ``owner/name``, or by URL.

Measured on a real service ecosystem, the links people write down most are a
repo named in a sentence, a comment or a path: "Managed by acme-kit", "lives in
acme-gateway", ``Path.home() / "GIT/acme-sidecar"``, an ArgoCD ``repoURL``, a
release download from ``github.com/owner/name/releases/download/<tag>/``. None of
those is an import, a host or a route; each names the repo it leans on.

Every repo-shaped token becomes a ``REPO_NAME`` fact; the join keeps only those
that name a checkout read here, so a word that merely looks like a repo name
costs a cache row, never a link. Bare names need a ``-`` (or, in a doc, a ``_``):
plain words and code identifiers are too common to read as repo names.

Facts are cached on disk, so the same guard as ``NAME`` holds: nothing is read
from an env file or a line that sets something secret-named, and a token with a
well-known credential prefix or a random-looking part is never a repo name.
"""

from __future__ import annotations

import re

from .mentions_net import _SECRETISH, _TOKEN_PREFIX, _random_part
from .mentions_sink import Sink
from .model import REPO_DEP, REPO_NAME, norm_repo

MAX_PER_FILE = 30  # a file names a handful of repos; past that it is listing words
_GITHUB = re.compile(
    r"github\.com[/:]([A-Za-z0-9][\w.-]{0,99})/([A-Za-z0-9][\w.-]{0,99}?)(?:\.git)?"
    r"(?=$|[/\s\"'`)#?:,;>\]])(/releases/download/([^/\s\"'`]{1,100})/)?"
)
_OWNER_NAME = re.compile(
    r"(?<![\w./@:-])([A-Za-z0-9][\w.-]{0,99})/([A-Za-z0-9][\w.-]{0,99})(?![\w/-])"
)
_HYPHEN = re.compile(r"(?<![\w.@-])([A-Za-z0-9][\w.]*(?:-[\w.]+)+)(?![\w-])")
_UNDERSCORE = re.compile(r"(?<![\w.@-])([A-Za-z0-9][A-Za-z0-9.]*(?:_[A-Za-z0-9.]+)+)(?![\w-])")
_EXT = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$")


def _credential(raw: str) -> bool:
    return bool(_TOKEN_PREFIX.match(raw)) or _random_part(raw)


def _name(token: str) -> str | None:
    token = token.rstrip(".")
    if not 5 <= len(token) <= 100 or _EXT.search(token) or ".." in token or _credential(token):
        return None
    return norm_repo(token)


def scan_repo_names(path: str, lines: list[str], sink: Sink, *, doc: bool, ci: bool) -> None:
    if path.rpartition("/")[2].lower().startswith(".env"):
        return  # an env file is where secrets live
    kind = "docs" if doc else "ci" if ci else "mention"
    added = 0
    for i, ln in enumerate(lines):
        if added >= MAX_PER_FILE:
            return
        if not ln.strip() or _SECRETISH.search(ln):
            continue
        n = i + 1
        line_kind = "deploy" if "repourl" in ln.lower() else kind
        for m in _GITHUB.finditer(ln):
            key = norm_repo(f"{m.group(1)}/{m.group(2)}")
            if m.group(4):  # a release asset: a pin on that tag
                sink.add(REPO_DEP, key, path, n, m.group(4))
            elif sink.add(REPO_NAME, key, path, n, line_kind):
                added += 1
        for m in _OWNER_NAME.finditer(ln):
            name = m.group(2).rstrip(".")  # the end of a sentence, not of the name
            if _EXT.search(name) or _credential(name) or _credential(m.group(1)):
                continue  # a file path, or something shaped like a credential
            if sink.add(REPO_NAME, norm_repo(f"{m.group(1)}/{name}"), path, n, line_kind):
                added += 1
        for rx in (_HYPHEN, _UNDERSCORE) if doc else (_HYPHEN,):
            for m in rx.finditer(ln):
                if sink.add(REPO_NAME, _name(m.group(1)), path, n, line_kind):
                    added += 1
