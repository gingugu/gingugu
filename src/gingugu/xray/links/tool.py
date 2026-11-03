"""``code_xray`` action ``links`` (X23), answered locally; and the brief's links section.

Which other repos on this machine lean on this one, by what, and where - and,
for named files, how far a change to them ripples across repos. Built where the
checkouts are: the brain is never asked and never sees a path. Shared by the
stdio handler, the proxy and ``gingugu xray``, like ``docs_tool``.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .. import hook_common, local, rank, scan
from . import index, join, render, ripple
from .model import Link

ACTIONS = ("links",)
MAX_LIMIT = 100
LINKS_BUDGET_S = 20.0  # an explicit ask: worth a cold read of every sibling
BRIEF_BUDGET_S = 3.0  # the brief can run inside a hook
MAX_DRIFT_CHECKS = 20
DRIFT_BUDGET_S = 5.0  # all drift checks together, two git calls each
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


_SHA = re.compile(r"[0-9a-f]{7,40}")


def _drift(eco: index.Ecosystem, lk: Link, timeout: float) -> str | None:
    """For a cite of ``repo@sha path``: "drifted" when the file changed since that
    sha, "unknown-sha" when that checkout does not have it, else None.

    The sha and path came out of another repo's text (and maybe a cache): the sha
    must be hex, and the path a file git listed in the cited checkout. The diff is
    run with no external diff or textconv driver, so a checkout's own config
    cannot make it run anything."""
    root = eco.roots.get(lk.dst)
    sha, path = lk.detail, lk.dst_path
    if root is None or not path or not _SHA.fullmatch(sha or ""):
        return None
    if path not in eco.repos[lk.dst].files:
        return None
    try:
        if scan.git(root, "cat-file", "-e", f"{sha}^{{commit}}", timeout=timeout).returncode:
            return "unknown-sha"
        proc = scan.git(
            root, "diff", "--quiet", "--no-ext-diff", "--no-textconv", sha, "HEAD", "--", path,
            timeout=timeout,
        )  # fmt: skip
        return "drifted" if proc.returncode == 1 else None
    except (scan.XrayError, OSError):
        return None


def _leave_summaries(eco: index.Ecosystem, res: join.Result, state: Path) -> None:
    """Every links read leaves the hooks their per-repo answer (``inbound``)."""
    from . import inbound

    try:
        inbound.write_all(eco.roots, res.links, state)
    except Exception:  # noqa: BLE001 - a summary never costs the answer
        pass


def _rank(lk: Link) -> tuple:
    return (not lk.certain, lk.kind, lk.src, lk.src_path, lk.src_line)


def build(
    eco: index.Ecosystem,
    res: join.Result,
    files: list[str] | None,
    limit: int = 20,
    closure=None,
    *,
    drift_s: float = DRIFT_BUDGET_S,
) -> dict:
    """Pure but for the cite drift check (``drift_s`` of git at most; 0 skips it):
    the links into and out of this repo, or (with files) the links into those
    files and the ripple from them."""
    me = eco.me
    n = min(max(0, limit), MAX_LIMIT)
    into = sorted((lk for lk in res.links if lk.dst == me), key=_rank)
    if files is not None:
        touched = closure(me, set(files)) if closure else set(files)
        into = [lk for lk in into if lk.dst_path is None or lk.dst_path in touched]
    out = sorted((lk for lk in res.links if lk.src == me), key=_rank)
    shown_in, shown_out = into[:n], ([] if files is not None else out[:n])
    drift: dict[int, str] = {}
    stop = time.monotonic() + drift_s
    for lk in [x for x in shown_in + shown_out if x.kind == "cite"][:MAX_DRIFT_CHECKS]:
        left = stop - time.monotonic()
        if left <= 0:
            break
        found = _drift(eco, lk, min(3.0, left))
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
    _leave_summaries(eco, res, state)
    data = build(eco, res, rel, limit, _closure(eco, state, session_id))
    return {"ok": True, **data, "text": render.text(data), "caveat": CAVEAT}


WARM_BUDGET_S = 600.0  # a deliberate warm-up, after an upgrade: read everything


def warm(path: str | None = None) -> dict:
    """Read every checkout under the sibling roots with no practical budget, so the
    facts caches and the hook summaries are full before any session needs them.
    An upgrade that changes an extractor retires every cached fact; this refills
    them. ``path``: any checkout to start from (default: the first under the roots)."""
    import time as _time

    from ...config import load_config
    from .. import repo_id

    if not enabled():
        raise ValueError("cross-repo links are off (MEMORY_XRAY_LINKS)")
    if path:
        repo = local.root(path)
    else:
        starts = [
            c for base in repo_id.roots() for c in sorted(base.iterdir()) if (c / ".git").exists()
        ]
        if not starts:
            return {"ok": True, "repos": 0, "seconds": 0.0, "text": "no checkouts under the roots"}
        repo = starts[0]
    state = load_config().db_path.parent / "hook-sessions"
    t = _time.monotonic()
    eco = index.read(repo, state, budget_s=WARM_BUDGET_S)
    res = join.join(eco.repos)
    _leave_summaries(eco, res, state)
    took = round(_time.monotonic() - t, 1)
    n = len(eco.repos)
    return {
        "ok": True, "repos": n, "links": len(res.links), "seconds": took,
        "text": f"warmed {n} checkout{'' if n == 1 else 's'} ({len(res.links)} links) in {took}s",
    }  # fmt: skip


def for_brief(repo: Path, state: Path, paths: list[str], session_id: str | None) -> dict | None:
    """The brief's section: who outside leans on the changed files, and the ripple.
    None when links are off, nothing changed, or anything fails: links never cost
    a brief."""
    if not enabled() or not paths:
        return None
    try:
        eco = index.read(repo, state, budget_s=BRIEF_BUDGET_S)
        res = join.join(eco.repos)
        _leave_summaries(eco, res, state)
        return build(eco, res, paths, 10, _closure(eco, state, session_id), drift_s=0)
    except Exception:  # noqa: BLE001
        return None
