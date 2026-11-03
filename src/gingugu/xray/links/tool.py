"""``code_xray`` action ``links`` (X23), answered locally; and the brief's links section.

Which other repos on this machine lean on this one, by what, and where - and,
for named files, how far a change to them ripples across repos. Built where the
checkouts are: the brain is never asked and never sees a path. Shared by the
stdio handler, the proxy and ``gingugu xray``, like ``docs_tool``.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import hook_common, local, rank, scan
from . import index, join, render, ripple
from .model import Link

ACTIONS = ("links",)
MAX_LIMIT = 100
LINKS_BUDGET_S = 20.0  # an explicit ask: worth a cold read of every sibling
BRIEF_BUDGET_S = 3.0  # the brief can run inside a hook
MAX_DRIFT_CHECKS = 20
CAVEAT = (
    "Links are inferred from names the code already uses: a host, route, image, "
    "package, table or repo one checkout provides and another mentions. Certain means "
    "exactly one repo here provides the name and the match is exact; anything looser is "
    "kept and flagged. Only checkouts on this machine are read, so a missing sibling is "
    "a missing link, not proof there is none."
)


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_LINKS", "on").lower() not in ("0", "off", "false")


def _closure(eco: index.Ecosystem, state: Path, session_id: str | None):
    """A repo's touched files widened by its own import blast. This checkout's
    graph is built if need be; a sibling's is used only when already cached."""
    graphs: dict[str, hook_common.Graph | None] = {}

    def widen(repo: str, files: set[str]) -> set[str]:
        if repo not in graphs:
            root = eco.roots.get(repo)
            try:
                if root is None:
                    graphs[repo] = None
                elif repo == eco.me:
                    graphs[repo] = hook_common.graph(root, state, session_id)
                else:
                    graphs[repo] = hook_common.cached_graph(root, state)
            except Exception:  # noqa: BLE001 - a graph never costs the links
                graphs[repo] = None
        g = graphs[repo]
        if g is None:
            return set(files)
        reach = rank.blast(sorted(files), g.files, g.links, g.ranks, hidden=g.packages)
        return set(files) | {r["path"] for r in reach["code"]}

    return widen


def _drift(eco: index.Ecosystem, lk: Link) -> str | None:
    """For a cite of ``repo@sha path``: "drifted" when the file changed since that
    sha, "unknown-sha" when this checkout does not have it, else None."""
    root = eco.roots.get(lk.dst)
    if root is None or not lk.dst_path or not lk.detail:
        return None
    try:
        if scan.git(root, "cat-file", "-e", f"{lk.detail}^{{commit}}", timeout=3).returncode:
            return "unknown-sha"
        proc = scan.git(root, "diff", "--quiet", lk.detail, "HEAD", "--", lk.dst_path, timeout=3)
        return "drifted" if proc.returncode == 1 else None
    except (scan.XrayError, OSError):
        return None


def _rank(lk: Link) -> tuple:
    return (not lk.certain, lk.kind, lk.src, lk.src_path, lk.src_line)


def build(
    eco: index.Ecosystem,
    res: join.Result,
    files: list[str] | None,
    limit: int = 20,
    closure=None,
) -> dict:
    """Pure but for the cite drift check: the links into and out of this repo, or
    (with files) the links into those files and the ripple from them."""
    me = eco.me
    n = min(max(0, limit), MAX_LIMIT)
    into = sorted((lk for lk in res.links if lk.dst == me), key=_rank)
    if files is not None:
        touched = closure(me, set(files)) if closure else set(files)
        into = [lk for lk in into if lk.dst_path is None or lk.dst_path in touched]
    out = sorted((lk for lk in res.links if lk.src == me), key=_rank)
    shown_in, shown_out = into[:n], ([] if files is not None else out[:n])
    drift: dict[int, str] = {}
    for lk in [x for x in shown_in + shown_out if x.kind == "cite"][:MAX_DRIFT_CHECKS]:
        found = _drift(eco, lk)
        if found:
            drift[id(lk)] = found
    data = {
        "repo": me,
        "read": {
            "repos": len(eco.repos),
            "partial": dict(eco.partial),
            "unscanned": list(eco.unscanned),
        },
        "leaning_on_this": [render.row(lk, drift.get(id(lk))) for lk in shown_in],
        "leaning_on_this_total": len(into),
        "shared": [render.shared_row(s) for s in res.shared if me in s.repos],
    }
    if files is None:
        data["this_leans_on"] = [render.row(lk, drift.get(id(lk))) for lk in shown_out]
        data["this_leans_on_total"] = len(out)
    else:
        data["files"] = list(files)
        reach = ripple.ripple(res.links, me, files, closure=closure)
        data["ripple"] = [render.reach_row(r) for r in reach]
    return data


def answer(
    path: str | None = None,
    files=None,
    limit: int = 20,
    *,
    session_id: str | None = None,
) -> dict:
    """The links of the checkout at ``path``; with ``files``, of those files."""
    from ...config import load_config

    if not enabled():
        raise ValueError("cross-repo links are off (MEMORY_XRAY_LINKS)")
    repo = local.root(path)
    named = local.file_list(files)
    rel = [p for p in local.seeds(named, repo) if p] if named else None
    state = load_config().db_path.parent / "hook-sessions"
    eco = index.read(repo, state, budget_s=LINKS_BUDGET_S)
    res = join.join(eco.repos)
    data = build(eco, res, rel, limit, _closure(eco, state, session_id))
    return {"ok": True, **data, "text": render.text(data), "caveat": CAVEAT}


def for_brief(repo: Path, state: Path, paths: list[str], session_id: str | None) -> dict | None:
    """The brief's section: who outside leans on the changed files, and the ripple.
    None when links are off, nothing changed, or anything fails: links never cost
    a brief."""
    if not enabled() or not paths:
        return None
    try:
        eco = index.read(repo, state, budget_s=BRIEF_BUDGET_S)
        res = join.join(eco.repos)
        return build(eco, res, paths, 10, _closure(eco, state, session_id))
    except Exception:  # noqa: BLE001
        return None
