"""X22: who cites a file - the questions asked of a docs map (see ``docs_map``).

A citation is a path mention in a doc that resolves to exactly one file of this
repo (X3's matcher): an ambiguous name resolves to nothing and cites nothing.
"""

from __future__ import annotations

from typing import NamedTuple

from . import scars
from .hook_common import _show

# A doc name is another checkout's choice: cut it before it reaches an agent.
MAX_NAME = 200


def label(doc: str, repo: str | None) -> str:
    """A citation as the agent reads it: ``doc``, or ``repo:doc`` from a sibling,
    quoted by ``_show`` and cut to ``MAX_NAME``."""
    name = doc if repo is None else f"{repo}:{doc}"
    if len(name) > MAX_NAME:
        name = name[:MAX_NAME] + "..."
    return _show(name)


class Cite(NamedTuple):
    doc: str
    repo: str | None  # None: this repo's own doc


class DocsMap(NamedTuple):
    files: frozenset[str]  # this repo's file list
    local: dict[str, list[str]]  # doc -> the path tokens it names
    siblings: dict[str, dict[str, list[str]]]  # sibling ident -> doc -> qualified paths


def _base(path: str) -> str:
    return path.rpartition("/")[2]


def _resolver(files: set[str] | frozenset[str]):
    """``scars._resolve`` against ``files``, looking only at those sharing the
    token's basename (a match must), so a big repo costs one pass, not one per token."""
    by_base: dict[str, set[str]] = {}
    for f in files:
        by_base.setdefault(_base(f), set()).add(f)

    def resolve(token: str) -> str | None:
        token = token.removeprefix("./")
        return scars._resolve(token, by_base.get(_base(token), set()))

    return resolve


def _cites(m: DocsMap, files: set[str] | frozenset[str], path: str | None) -> dict[str, list[Cite]]:
    """path -> its citations (sorted); with ``path``, just that one."""
    resolve = _resolver(files)
    hits: dict[str, set[Cite]] = {}
    sources = [(None, m.local)] + list(m.siblings.items())
    for repo, docs in sources:
        for doc, refs in docs.items():
            for ref in refs:
                if path is not None and _base(ref.removeprefix("./")) != _base(path):
                    continue
                target = resolve(ref)
                if not target or (path is not None and target != path):
                    continue
                if repo is None and target == doc:
                    continue  # a doc never cites itself
                hits.setdefault(target, set()).add(Cite(doc, repo))
    return {p: sorted(c, key=lambda c: (c.repo or "", c.doc)) for p, c in hits.items()}


def citing(m: DocsMap, path: str) -> list[Cite]:
    """The docs that name ``path``, a doc never citing itself. An ambiguous name
    resolves to nothing and cites nothing."""
    return _cites(m, m.files, path).get(path, [])


def citing_gone(m: DocsMap, path: str) -> list[Cite]:
    """As ``citing``, for a file the change deleted or renamed away."""
    return _cites(m, m.files | {path}, path).get(path, [])


def cited(m: DocsMap) -> dict[str, list[Cite]]:
    """Every file of this repo that some doc cites, with its citations."""
    return _cites(m, m.files, None)
