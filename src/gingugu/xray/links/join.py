"""X23: join every repo's mentions to the repos that provide them.

A mention in B of a name A provides is a link B -> A. A link is *certain* when
exactly one other repo provides the name, by an exact match. It is kept but
marked uncertain, with a flag saying why, when the match is looser:

- ``ambiguous``: more than one repo provides the name (or shares a bare repo name);
- ``also-own``: the mentioning repo provides the name as well (a vendored spec);
- ``suffix``: a route matched on its tail (the provider's mount prefix is unseen);
- ``wildcard``/``pattern``/``partial``: a ``*.x`` host, a globbed resource name,
  or an image matched on its last segment only.

Private hosts (``.internal``, ``.svc.cluster.local``...) that two or more repos
call and none here serves are returned as ``shared``: a service outside this
set, worth knowing, with no direction to give it.
"""

from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatchcase
from typing import NamedTuple

from ..docs_cites import _resolver
from . import model
from .model import Fact, Link

_REPO_KIND = {model.CI: "ci", model.SUBMODULE: "source", model.REPO_DEP: "pin"}
# Flags that make a link uncertain; "unpinned" and "missing" describe a certain one.
UNCERTAIN = frozenset(
    {"ambiguous", "by-name", "also-own", "suffix", "unmounted", "route-only", "wildcard",
     "pattern", "partial"}
)  # fmt: skip
_PRIVATE = (
    ".internal", ".local", ".corp", ".lan", ".intranet", ".svc", ".svc.cluster.local",
    ".home.arpa", ".private",
)  # fmt: skip


class RepoFacts(NamedTuple):
    provided: list[Fact]
    mentioned: list[Fact]
    files: frozenset[str]


class Shared(NamedTuple):
    key: str
    repos: tuple[str, ...]
    evidence: tuple[tuple[str, str, int], ...]  # (repo, path, line), first per repo


class Result(NamedTuple):
    links: list[Link]
    shared: list[Shared]


def _segs(route: str) -> list[str]:
    return [s for s in route.split("/") if s]


def _seg_eq(mention: list[str], provided: list[str]) -> bool:
    """A provider's parameter takes any segment; every name it gives must be
    matched by that name. A mention's ``*`` is unknown, so it matches only a
    parameter: ``/messages/*`` is not ``/*/message``."""
    if len(mention) != len(provided):
        return False
    return all(p == model.WILD or m == p for m, p in zip(mention, provided, strict=True))


class _Index:
    """Every provided name, by kind, with the repos and facts behind it."""

    def __init__(self, repos: dict[str, RepoFacts]):
        self.by: dict[str, dict[str, list[tuple[str, Fact]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.globs: list[tuple[str, Fact]] = []  # resources with a "*"
        self.wild_hosts: list[tuple[str, Fact]] = []
        self.routes_by_last: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
        self.image_by_last: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
        for rid, facts in repos.items():
            for f in facts.provided:
                self.by[f.kind][f.key].append((rid, f))
                if f.kind == model.RESOURCE and "*" in f.key:
                    self.globs.append((rid, f))
                elif f.kind == model.HOST and f.key.startswith("*."):
                    self.wild_hosts.append((rid, f))
                elif f.kind == model.ROUTE and f.key not in model.GENERIC_ROUTES:
                    last = (_segs(f.key) or [""])[-1]
                    self.routes_by_last[last].append((rid, f))
                elif f.kind == model.IMAGE:
                    self.image_by_last[f.key.rpartition("/")[2]].append((rid, f))
        self.idents = sorted(repos)
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for rid in self.idents:
            self.by_name[rid.rpartition("/")[2]].append(rid)


def _repos_named(ix: _Index, key: str) -> tuple[list[str], bool]:
    """The repos a repo key names, and whether that reading is certain."""
    if "/" in key:
        if key in ix.by_name.get(key.rpartition("/")[2], []):
            return [key], True
        # a sibling known only by its folder name may be this owner/name
        folder = [r for r in ix.by_name.get(key.rpartition("/")[2], []) if "/" not in r]
        return folder, False
    found = ix.by_name.get(key, [])
    return list(found), len(found) == 1


def _route_hits(ix: _Index, key: str) -> list[tuple[str, Fact, bool]]:
    """(repo, provider fact, exact) for a mentioned route."""
    if key in model.GENERIC_ROUTES or model.concrete(key) == 0:
        return []
    m = _segs(key)
    cands = ix.routes_by_last.get(model.WILD, [])  # a provider parameter takes anything
    if m[-1] != model.WILD:
        cands = ix.routes_by_last.get(m[-1], []) + cands
    out = []
    for rid, f in cands:
        r = _segs(f.key)
        if _seg_eq(m, r) and model.concrete(f.key) >= 1:
            out.append((rid, f, True))
        elif len(m) > len(r) and model.concrete(f.key) >= 2 and _seg_eq(m[-len(r) :], r):
            out.append((rid, f, False))
    return out


def _provided_kind(kind: str) -> str:
    return {model.DEP: model.PACKAGE, model.NAME: model.RESOURCE}.get(kind, kind)


def _named(ix: _Index, m: Fact) -> list[tuple[str, Fact, str | None]]:
    """(repo, provider fact, loose-match flag or None) for a named mention."""
    out = [(rid, f, None) for rid, f in ix.by[_provided_kind(m.kind)].get(m.key, [])]
    if m.kind == model.HOST:
        out += [(r, f, "wildcard") for r, f in ix.wild_hosts if m.key.endswith(f.key[1:])]
    elif m.kind == model.NAME:
        out += [(r, f, "pattern") for r, f in ix.globs if fnmatchcase(m.key, f.key)]
    elif m.kind == model.IMAGE:
        last = m.key.rpartition("/")[2]
        out += [(r, f, "partial") for r, f in ix.image_by_last.get(last, []) if f.key != m.key]
    return out


def _route_links(rid: str, m: Fact, ix: _Index, hosts_in) -> list[tuple[str, Fact, str | None]]:
    """A route is a weak name on its own: unrelated services share ``/api/users``.
    It is corroborated when the mentioning repo names a host the provider serves
    (the same file, for a standard OpenAI-style path). Corroborated: an exact
    match on a whole path is certain, a tail match is kept flagged. Not
    corroborated: only an exact match on a whole path with two or more names in
    it survives, flagged ``route-only``."""
    out = []
    for r, f, exact in _route_hits(ix, m.key):
        whole = f.detail != model.UNMOUNTED
        if m.key in model.STANDARD_ROUTES or f.key in model.STANDARD_ROUTES:
            if r in hosts_in.get((rid, m.path), ()):
                out.append((r, f, None if exact and whole else "suffix"))
            continue
        if r in hosts_in.get((rid, None), ()):
            loose = None if exact and whole else ("unmounted" if exact else "suffix")
            out.append((r, f, loose))
        elif exact and whole and model.concrete(f.key) >= 2:
            out.append((r, f, "route-only"))
    return out


_LINK_KIND = {
    model.DEP: "pin", model.IMAGE: "deploy", model.HOST: "runtime",
    model.ROUTE: "runtime", model.NAME: "data",
}  # fmt: skip


def _for(rid: str, m: Fact, ix: _Index, repos: dict[str, RepoFacts], hosts_in) -> list[Link]:
    """The links one mention makes."""
    if m.kind in _REPO_KIND or m.kind in (model.CITE, model.DOC):
        targets, sure = _repos_named(ix, m.key)
        targets = [t for t in targets if t != rid]
        out = []
        for t in targets:
            if len(targets) > 1:
                flags = ["ambiguous"]
            else:
                flags = [] if sure else ["by-name"]  # a folder-name guess at its identity
            dst_path = None
            if m.kind in (model.CI, model.REPO_DEP) and not model.pinned(m.detail):
                flags.append("unpinned")
            if m.kind == model.CITE and m.target:
                if m.target in repos[t].files:
                    dst_path = m.target
                else:
                    flags.append("missing")
            if m.kind == model.DOC:
                dst_path = _resolver(repos[t].files)(m.target) or None
                if dst_path is None:
                    continue
            kind = _REPO_KIND.get(m.kind) or ("cite" if m.kind == model.CITE else "docs")
            out.append(_link(rid, m, t, dst_path, kind, flags))
        return out
    if m.kind == model.ROUTE:
        hits = _route_links(rid, m, ix, hosts_in)
    else:
        hits = _named(ix, m)
    hits = [h for h in hits if h[0] != rid]
    own = any(r == rid for r, _f in ix.by[_provided_kind(m.kind)].get(m.key, []))
    providers = {r for r, _f, _x in hits}
    out = []
    for r, f, loose in hits:
        flags = [loose] if loose else []
        if len(providers) > 1:
            flags.append("ambiguous")
        if own:
            flags.append("also-own")
        out.append(_link(rid, m, r, f.path, _LINK_KIND[m.kind], flags))
    return out


def _link(rid: str, m: Fact, dst: str, dst_path: str | None, kind: str, flags: list[str]) -> Link:
    certain = not any(fl in UNCERTAIN for fl in flags)
    return Link(rid, m.path, m.line, dst, dst_path, kind, m.key, m.detail, certain, tuple(flags))


def _shared(repos: dict[str, RepoFacts], ix: _Index) -> list[Shared]:
    seen: dict[str, dict[str, tuple[str, int]]] = defaultdict(dict)
    for rid, facts in repos.items():
        for m in facts.mentioned:
            if m.kind != model.HOST or not m.key.endswith(_PRIVATE):
                continue
            if ix.by[model.HOST].get(m.key) or any(
                m.key.endswith(f.key[1:]) for _r, f in ix.wild_hosts
            ):
                continue
            prev = seen[m.key].get(rid)
            if prev is None or (m.path, m.line) < prev:
                seen[m.key][rid] = (m.path, m.line)
    out = []
    for key, by_repo in sorted(seen.items()):
        if len(by_repo) >= 2:
            rs = tuple(sorted(by_repo))
            out.append(Shared(key, rs, tuple((r, *by_repo[r]) for r in rs)))
    return out


def join(repos: dict[str, RepoFacts]) -> Result:
    """Every link between the repos, deduplicated (first line wins), sorted."""
    ix = _Index(repos)
    # (repo, file) and (repo, None) -> the repos serving a host it names there / anywhere
    hosts_in: dict[tuple[str, str | None], set[str]] = defaultdict(set)
    for rid, facts in repos.items():
        for m in facts.mentioned:
            if m.kind == model.HOST:
                for r, _f, _x in _named(ix, m):
                    hosts_in[(rid, m.path)].add(r)
                    hosts_in[(rid, None)].add(r)
    best: dict[tuple, Link] = {}
    for rid, facts in repos.items():
        for m in facts.mentioned:
            for lk in _for(rid, m, ix, repos, hosts_in):
                k = (lk.src, lk.src_path, lk.dst, lk.dst_path, lk.kind, lk.key)
                if k not in best or lk.src_line < best[k].src_line:
                    best[k] = lk
    found = sorted(
        best.values(),
        key=lambda lk: (lk.dst, lk.dst_path or "", lk.src, lk.src_path, lk.src_line, lk.kind),
    )
    return Result(found, _shared(repos, ix))
