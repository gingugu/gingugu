"""``code_xray`` action ``routes`` (X7b), answered locally.

Which HTTP routes a file serves and who in this repo calls each; which routes it
calls and what serves each; or, with no files, every route by its callers, the
calls served nowhere here first. Built where the checkout is, so the brain is
never asked and never sees a path. Shared by the stdio handler, the proxy and
``gingugu xray``, like ``table_tool``.
"""

from __future__ import annotations

from collections import defaultdict

from . import local, route_map
from .brief import _safe
from .hook_common import _show
from .route_join import caller_class
from .route_show import Group, caller_key, display, groups_of, line_of

ACTIONS = ("routes",)
MAX_LIMIT = 200
MAX_CHARS = 8800
MAX_LOCS = 10
SHOWN = 3
CAVEAT = (
    "Only literal route strings are seen: a URL built from pieces is missed. Routes come "
    "from the X-ray entry points, so a framework it does not read serves nothing. A path "
    "inside a URL to another host is never counted. 'Served nowhere here' can mean another "
    "service serves it. Names and line numbers only."
)


def _fit(lines: list[str]) -> str:
    text = "\n".join(lines)
    if len(text) <= MAX_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_CHARS)  # whole lines, never one cut mid-way
    return text[: cut if cut > 0 else MAX_CHARS]


def _where(locs: list[dict], none: str) -> str:
    if not locs:
        return none
    shown = ", ".join(f"{_show(w['path'])}:{w['line']}" for w in locs[:SHOWN])
    more = f" (+{len(locs) - SHOWN} more)" if len(locs) > SHOWN else ""
    return shown + more


def _served_line(c: dict) -> str:
    by = ", ".join(
        f"{_show(s['path'])}:{s['line']}" + (f" ({_show(s['methods'])})" if s["methods"] else "")
        for s in c["served_by"][:SHOWN]
    )
    more = f" (+{len(c['served_by']) - SHOWN} more)" if len(c["served_by"]) > SHOWN else ""
    tail = " - ambiguous" if c["ambiguous"] else ""
    return f"  calls {_show(c['route'])}: served by {by}{more}{tail}"


def _file_lines(f: dict) -> list[str]:
    quiet = f["serves"] or f["calls"] or f["unserved"]
    lines = [f"- {_show(f['path'])}" + ("" if quiet else ": none")]
    for s in f["serves"]:
        who = _where(s["called_by"], "")
        text = f"called by {who}" if who else "no caller here"
        lines.append(f"  serves {_show(s['route'])}: {text}")
    for c in f["calls"]:
        lines.append(_served_line(c))
    for u in f["unserved"]:
        lines.append(f"  calls {_show(u['route'])}: served nowhere here")
    return lines


def render(data: dict, name: str) -> str:
    """Compact text for an agent; every path and route is quoted by ``_show``."""
    if "files" in data:
        n = len(data["files"])
        lines = [f"GINGUGU X-RAY ROUTES: {_show(name)} - routes of {n} named files"]
        for f in data["files"]:
            lines.extend(_file_lines(f))
        return _fit(lines)
    t = data["totals"]
    head = (
        f"GINGUGU X-RAY ROUTES: {_show(name)} - {t['routes']} routes served, {t['edges']} calls "
        f"joined ({t['code_edges']} from code), {t['unserved']} served nowhere here, "
        f"{t['uncalled']} with no caller here."
    )
    lines = [head + ("" if t["complete"] else " Not every file was read.")]
    for u in data["unserved"]:
        lines.append(
            f"- calls {_show(u['route'])} ({_show(u['path'])}:{u['line']}): served nowhere here"
        )
    for r in data["routes"]:
        who = f"called from {r['files']} files" if r["files"] else "no caller here"
        lines.append(f"- {_show(r['route'])} ({_show(r['path'])}:{r['line']}): {who}")
    return _fit(lines)


def _file(m: route_map.RouteMap, rel: str) -> dict:
    mine = groups_of(s for s in m.serves if s.path == rel)
    in_edges = route_map.served_in(m, rel)
    serves = []
    for k, group in mine.items():
        found = {
            (e.call.path, e.call.line)
            for e in in_edges
            if (e.serve.path, e.serve.head, e.serve.key) == k
        }
        callers = sorted(found, key=lambda pl: caller_key(*pl))
        serves.append(
            {
                "route": display(group),
                "line": line_of(group),
                "called_by": [{"path": _safe(p), "line": n} for p, n in callers[:MAX_LOCS]],
            }
        )
    by_key: dict[str, list] = defaultdict(list)
    for e in route_map.calls_in(m, rel):
        by_key[e.call.key].append(e)
    calls = []
    for key, edges in by_key.items():
        seen: dict[tuple[str, int], str] = {}
        for e in edges:
            seen.setdefault((e.serve.path, e.serve.line), e.methods)
        calls.append(
            {
                "route": key,
                "line": min(e.call.line for e in edges),
                "served_by": [
                    {"path": _safe(p), "line": n, "methods": mt}
                    for (p, n), mt in list(seen.items())[:MAX_LOCS]
                ],
                "ambiguous": any(e.ambiguous for e in edges),
            }
        )
    unserved = [{"route": u.key, "line": u.line} for u in m.unserved if u.path == rel]
    return {
        "path": _safe(rel),
        "serves": sorted(serves, key=lambda r: r["line"]),
        "calls": sorted(calls, key=lambda r: r["line"]),
        "unserved": sorted(unserved, key=lambda r: r["line"]),
    }


def _repo(m: route_map.RouteMap, limit: int) -> dict:
    groups = groups_of(m.serves)
    hits: dict[Group, list] = defaultdict(list)
    for e in m.edges:
        hits[(e.serve.path, e.serve.head, e.serve.key)].append(e)
    rows = []
    for k, group in groups.items():
        edges = hits.get(k, [])
        rows.append(
            {
                "route": display(group),
                "path": _safe(k[0]),
                "line": line_of(group),
                "callers": len(edges),
                "files": len({e.call.path for e in edges}),
            }
        )
    rows.sort(key=lambda r: (-r["files"], -r["callers"], r["path"], r["line"]))
    cap = min(max(0, limit), MAX_LIMIT)
    miss = [{"route": u.key, "path": _safe(u.path), "line": u.line} for u in m.unserved]
    return {
        "routes": rows[:cap],
        "unserved": miss[:cap],
        "totals": {
            "served": len(m.serves),
            "routes": len(groups),
            "edges": len(m.edges),
            "code_edges": sum(caller_class(e.call.path) == "code" for e in m.edges),
            "unserved": len(m.unserved),
            "uncalled": sum(1 for k in groups if k not in hits),
            "complete": m.complete,
        },
    }


def answer(
    path: str | None = None,
    files=None,
    limit: int = 50,
    *,
    session_id: str | None = None,
) -> dict:
    """The routes the named files serve and call, or (no files) every route."""
    from ..config import load_config

    if not route_map.enabled():
        raise ValueError("the route map is off (MEMORY_XRAY_ROUTES)")
    repo = local.root(path)
    named = local.file_list(files)
    rel = [p for p in local.seeds(named, repo) if p] if named else None
    state = load_config().db_path.parent / "hook-sessions"
    m = route_map.read(repo, state, session_id=session_id)
    data = {"files": [_file(m, p) for p in rel]} if rel else _repo(m, limit)
    return {"ok": True, **data, "text": render(data, repo.name), "caveat": CAVEAT}
