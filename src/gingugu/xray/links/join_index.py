"""X23: the provider index the join matches mentions against, and its matchers.

Every provided name by kind, plus buckets for the loose matches (globbed
resource names by a literal end, wildcard hosts, routes by their last segment,
images by their last path part), and the repo-identity reader.
"""

from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatchcase
from typing import NamedTuple

from . import model
from .model import Fact


class RepoFacts(NamedTuple):
    provided: list[Fact]
    mentioned: list[Fact]
    files: frozenset[str]


def _segs(route: str) -> list[str]:
    return [s for s in route.split("/") if s]


def _seg_eq(mention: list[str], provided: list[str]) -> bool:
    """A provider's parameter takes any segment; every name it gives must be
    matched by that name. A mention's ``*`` is unknown, so it matches only a
    parameter: ``/messages/*`` is not ``/*/message``."""
    if len(mention) != len(provided):
        return False
    return all(p == model.WILD or m == p for m, p in zip(mention, provided, strict=True))


_GLOB_K = 3  # characters of a glob's literal end (or start) it is bucketed by


class _Index:
    """Every provided name, by kind, with the repos and facts behind it."""

    def __init__(self, repos: dict[str, RepoFacts]):
        self.by: dict[str, dict[str, list[tuple[str, Fact]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # resources with a "*", bucketed by a literal end so a name meets only the
        # globs it could match (measured: 13M fnmatch calls on a 48-repo machine)
        self.glob_by_end: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
        self.glob_by_start: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
        self.glob_rest: list[tuple[str, Fact]] = []
        self.wild_hosts: list[tuple[str, Fact]] = []
        self.routes_by_last: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
        self.image_by_last: dict[str, list[tuple[str, Fact]]] = defaultdict(list)
        for rid, facts in repos.items():
            for f in facts.provided:
                self.by[f.kind][f.key].append((rid, f))
                if f.kind == model.RESOURCE and "*" in f.key:
                    start, end = f.key.split("*", 1)[0], f.key.rsplit("*", 1)[1]
                    if len(end) >= _GLOB_K:
                        self.glob_by_end[end[-_GLOB_K:]].append((rid, f))
                    elif len(start) >= _GLOB_K:
                        self.glob_by_start[start[:_GLOB_K]].append((rid, f))
                    else:
                        self.glob_rest.append((rid, f))
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
        k = m.key
        cands = (
            ix.glob_by_end.get(k[-_GLOB_K:], [])
            + ix.glob_by_start.get(k[:_GLOB_K], [])
            + ix.glob_rest
        )
        out += [(r, f, "pattern") for r, f in cands if fnmatchcase(k, f.key)]
    elif m.kind == model.IMAGE:
        last = m.key.rpartition("/")[2]
        out += [(r, f, "partial") for r, f in ix.image_by_last.get(last, []) if f.key != m.key]
    return out
