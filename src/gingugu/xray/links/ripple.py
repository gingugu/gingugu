"""X23: ripple - who leans on a change, then who leans on them, across repos.

From the changed files of one repo, follow links backwards. A file-level link
counts when the file it points at is touched. A pin or a deploy leans on what a
repo *ships* - its package, its image - so it counts whenever a change reaches
that repo at all. Any other repo-level link (a CI checkout, a submodule) counts
only into the repo that changed: it leans on the repo's source as it is, which
a ripple further out has not changed. In each repo
reached, the touched files are the evidence files of the links that reached it,
widened by that repo's own import blast when ``closure`` can supply one.

Breadth first, each repo once, at its nearest hop. Pure.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import NamedTuple

from .model import Link

HOPS = 3
SHIPPED = frozenset({"pin", "deploy"})  # lean on what a repo ships, not on one file
MAX_VIA = 20  # evidence links kept per repo reached

Closure = Callable[[str, set[str]], set[str]]


class Reach(NamedTuple):
    repo: str
    hop: int
    certain: bool  # some link that reached it is certain
    via: tuple[Link, ...]  # the links that reached it, best first


def _into(links: Iterable[Link]) -> dict[str, list[Link]]:
    by_dst: dict[str, list[Link]] = {}
    for lk in links:
        if lk.src != lk.dst:
            by_dst.setdefault(lk.dst, []).append(lk)
    return by_dst


def ripple(
    links: list[Link],
    start: str,
    files: list[str] | None,
    *,
    closure: Closure | None = None,
    hops: int = HOPS,
) -> list[Reach]:
    """Every repo a change to ``files`` of ``start`` reaches, by hop then name.
    ``files=None`` asks about the repo as a whole: every link into it counts."""
    by_dst = _into(links)
    touched: dict[str, set[str] | None] = {start: None}
    if files is not None:
        seeds = set(files)
        touched[start] = closure(start, seeds) if closure else seeds
    done: set[str] = {start}
    frontier = [start]
    out: list[Reach] = []
    for hop in range(1, hops + 1):
        found: dict[str, list[Link]] = {}
        for repo in frontier:
            have = touched[repo]
            for lk in by_dst.get(repo, ()):
                if lk.src in done:
                    continue
                if lk.kind in SHIPPED:
                    pass
                elif lk.dst_path is None:
                    if repo != start:
                        continue
                elif have is not None and lk.dst_path not in have:
                    continue
                found.setdefault(lk.src, []).append(lk)
        if not found:
            break
        frontier = sorted(found)
        for repo in frontier:
            via = sorted(found[repo], key=lambda lk: (not lk.certain, lk.src_path, lk.src_line))
            evidence = {lk.src_path for lk in via}
            touched[repo] = closure(repo, evidence) if closure else evidence
            done.add(repo)
            out.append(Reach(repo, hop, any(lk.certain for lk in via), tuple(via[:MAX_VIA])))
    return out
