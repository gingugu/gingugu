"""X4 hotspots and coupling, X6 blind spots: the import graph read against history.

Pure functions over a ``hook_common.Graph`` and a ``history.History``.

**Hotspots** are where work concentrates on load-bearing code: churn x rank x
authors. Every factor is a product term, so no factor needs scaling to be
comparable and the order cannot hinge on a constant.

**Coupling** is code-maat's logical coupling, its published defaults included:
both files changed in at least ``MIN_REVS`` commits, together in at least
``history.MIN_SHARED``, and a degree - shared commits over the average of the
two revision counts - of at least ``MIN_DEGREE``. A pair is ``hidden`` when
neither file imports the other: the dependency the import graph cannot show.
Given ``tracked`` (the files git lists), coupling also covers tracked files
outside the graph - YAML, HCL - so a pair involving one is always hidden;
``others`` ranks those files by churn x authors, apart from the import-ranked
hotspots, since import rank means nothing for them.

**Blind spots** are where a change reaches furthest with the least review:
imported source no test reaches directly and no test is named for, ordered by
rank over the number of people who have touched it, fewest commits first on a
tie. Measured on httpx, a test importing only the package would mark 20 of 23
files untested; the naming rule (``hook_common.nearest_tests``'s) brings it to 8.
"""

from __future__ import annotations

from collections.abc import Iterable

from . import hook_common
from .history import MIN_SHARED, History
from .hook_common import Graph

# code-maat cmd_line.clj: --min-revs 5, --min-coupling 30 (percent).
MIN_REVS = 5
MIN_DEGREE = 0.30


def _source(g: Graph) -> list[str]:
    """Code files a change can land in: not tests, packages or package initializers."""
    return [
        p
        for p, is_test in g.files.items()
        if not is_test and p not in g.packages and p.rsplit("/", 1)[-1] != "__init__.py"
    ]


def _positions(g: Graph) -> dict[str, int]:
    ranked = sorted((p for p in g.files if p not in g.packages), key=lambda p: (-g.ranks[p], p))
    return {p: n for n, p in enumerate(ranked, 1)}


def hotspots(g: Graph, h: History) -> list[dict]:
    """Changed source by churn x rank x authors, highest first."""
    position = _positions(g)
    imported_by = hook_common.importers(g)
    touched = [p for p in _source(g) if h.churn.get(p)]
    touched.sort(key=lambda p: (-h.churn[p] * g.ranks[p] * h.authors.get(p, 1), p))
    return [
        {
            "path": p,
            "rank": position.get(p),
            "commits": h.churn[p],
            "authors": h.authors.get(p, 0),
            "importers": len(imported_by.get(p, ())),
        }
        for p in touched
    ]


def others(g: Graph, h: History, tracked: Iterable[str]) -> list[dict]:
    """Tracked files outside the import graph that changed, by churn x authors."""
    touched = [p for p in tracked if p not in g.files and h.churn.get(p, 0) > 0]
    touched.sort(key=lambda p: (-(h.churn[p] * max(h.authors.get(p, 0), 1)), p))
    return [{"path": p, "commits": h.churn[p], "authors": h.authors.get(p, 0)} for p in touched]


def _linked(g: Graph) -> set[tuple[str, str]]:
    return {(ln.src, ln.dst) for ln in g.links if not ln.implicit}


def coupling(
    g: Graph, h: History, files: list[str] | None = None, tracked: Iterable[str] | None = None
) -> list[dict]:
    """Pairs of files that change together, strongest first; with ``files``, only
    pairs that include one of them. Graph files only, unless ``tracked`` adds the
    tracked files outside the graph."""
    linked = _linked(g)
    focus = set(files) if files else None
    extra = set(tracked) - set(g.files) if tracked is not None else set()

    def eligible(p: str) -> bool:
        return (p in g.files and p not in g.packages) or p in extra

    rows = []
    for (a, b), shared in h.pairs.items():
        if not eligible(a) or not eligible(b):
            continue
        if focus is not None and a not in focus and b not in focus:
            continue
        ra, rb = h.churn.get(a, 0), h.churn.get(b, 0)
        if ra < MIN_REVS or rb < MIN_REVS or shared < MIN_SHARED:
            continue
        degree = shared / ((ra + rb) / 2)
        if degree < MIN_DEGREE:
            continue
        hidden = (a, b) not in linked and (b, a) not in linked
        rows.append({"a": a, "b": b, "shared": shared, "degree": degree, "hidden": hidden})
    rows.sort(key=lambda r: (-r["degree"], -r["shared"], r["a"], r["b"]))
    return rows


def partners(g: Graph, h: History, rel: str) -> list[tuple[str, float]]:
    """The files ``rel`` changes with though neither imports the other."""
    out = []
    for r in coupling(g, h, files=[rel]):
        if r["hidden"]:
            out.append((r["b"] if r["a"] == rel else r["a"], r["degree"]))
    return sorted(out, key=lambda pd: (-pd[1], pd[0]))


def _stem(path: str) -> list[str]:
    parts = path.rsplit(".", 1)[0].split("/")
    name = parts[-2] if parts[-1] == "__init__" and len(parts) > 1 else parts[-1]
    return hook_common._words(name + ".py")


def tested(g: Graph) -> set[str]:
    """Files a test imports directly, or that a test file is named for."""
    out = {
        ln.dst
        for ln in g.links
        if g.files.get(ln.src) and not ln.implicit and ln.dst in g.files and not g.files[ln.dst]
    }
    tests = [p for p, is_test in g.files.items() if is_test]
    for p, is_test in g.files.items():
        if is_test or p in out:
            continue
        stem = _stem(p)
        if stem and any(hook_common._named_for(t, stem) == 0 for t in tests):
            out.add(p)
    return out


def blindspots(g: Graph, h: History) -> list[dict]:
    """Imported, untested source, by rank over authors; fewer commits first on a tie."""
    position = _positions(g)
    imported_by = hook_common.importers(g)
    covered = tested(g)
    blind = [p for p in _source(g) if imported_by.get(p) and p not in covered]

    def key(p: str) -> tuple:
        return (-g.ranks[p] / max(h.authors.get(p, 0), 1), h.churn.get(p, 0), p)

    return [
        {
            "path": p,
            "rank": position.get(p),
            "importers": len(imported_by[p]),
            "commits": h.churn.get(p, 0),
            "authors": h.authors.get(p, 0),
            "single_author": h.authors.get(p, 0) == 1,
        }
        for p in sorted(blind, key=key)
    ]
