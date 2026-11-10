"""``code_xray`` action ``surface`` (X7d), answered locally: the attack-surface map.

Every entry point (an HTTP route, an MCP tool, a CLI command, a queued task, a
Go ``main``) with the sinks it reaches through the call graph - command
execution, code evaluation, deserialisation, built SQL, file writes, secrets,
network, database tables - most dangerous and nearest first, each with the
shortest path. Then the sinks no entry reaches. With files: only the entries in
them, or that reach into them.

Built where the checkout is, from the hook symbol cache and the X7c table map,
so the brain is never asked and never sees a path. Shared by the stdio
handler, the proxy and ``gingugu xray``, like ``table_tool``.
"""

from __future__ import annotations

from . import hook_common, local, surface, table_map
from .brief import _safe
from .hook_common import _show
from .lang import is_test
from .symbols import label

ACTIONS = ("surface",)
MAX_LIMIT = 200
MAX_REACH = 20  # per entry in the answer; the total is always whole
SHOWN = 3  # per entry in the text
MAX_CHARS = 8800
CAVEAT = (
    "Static call graph (Python, TS/JS, Go), a lower bound: a handler reached through "
    "an untyped value, an interface or a callback can have more reach than listed, and "
    "an entry registered in a form the catalogue does not know is not listed. A listed "
    "path exists in the code; whether outside input flows along it is not checked. "
    "'sql.built' is a query assembled at runtime from a part not known safe, not a "
    "proven injection. Test files are left out."
)


def _reach(r: surface.Reach) -> dict:
    return {
        "sink": r.sink,
        "category": r.category,
        "path": _safe(r.path),
        "symbol": label(r.symbol),
        "line": r.line,
        "hops": r.hops,
        "via": [label(q) for q in r.via[: surface.MAX_VIA]],
    }


def _entry(e, found: list[surface.Reach]) -> dict:
    return {
        "path": _safe(e.path),
        "line": e.line,
        "label": e.label,
        "kind": e.kind,
        "handler": label(e.handler),
        "inline": e.inline,
        "resolved": bool(e.nodes) or e.inline,
        "reach": [_reach(r) for r in found[:MAX_REACH]],
        "reach_total": len(found),
    }


def _reach_line(r: dict) -> str:
    where = f"{_show(r['path'])}:{r['line']}"
    if r["hops"] == 0:
        return f"{r['category']} {r['sink']} at {where} (in the handler)"
    path = " > ".join(r["via"]) + (" > ..." if r["hops"] >= surface.MAX_VIA else "")
    hops = f"{r['hops']} hop" + ("s" if r["hops"] > 1 else "")
    return f"{r['category']} {r['sink']} at {where} ({hops}: {path})"


def _fit(lines: list[str]) -> str:
    text = "\n".join(lines)
    if len(text) <= MAX_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_CHARS)  # whole lines, never one cut mid-way
    return text[: cut if cut > 0 else MAX_CHARS]


def render(data: dict, name: str) -> str:
    """Compact text for an agent; every path is quoted by ``_show``."""
    t = data["totals"]
    lines = [
        f"GINGUGU X-RAY SURFACE: {_show(name)} - {t['entries']} entry points "
        f"({t['reaching']} reach a sink, {t['unresolved']} with a handler not resolved), "
        f"{t['sinks']} sinks ({t['unreached']} no entry reaches). Most dangerous first:"
    ]
    for e in data["entries"]:
        how = "inline in " if e["inline"] else ("" if e["resolved"] else "unresolved ")
        head = f"- {e['kind']} {e['label']} {_show(e['path'])}:{e['line']} -> {how}{e['handler']}"
        if not e["reach"]:
            lines.append(head + ": no sink reached")
            continue
        more = e["reach_total"] - SHOWN
        lines.append(head + (f" ({more} more sinks)" if more > 0 else ""))
        lines.extend(f"    {_reach_line(r)}" for r in e["reach"][:SHOWN])
    if data["unreached"]:
        lines.append("Sinks no entry reaches (internal, or reached in ways the graph cannot see):")
        lines.extend(
            f"- {u['category']} {u['sink']} at {_show(u['path'])}:{u['line']} in {u['symbol']}"
            for u in data["unreached"]
        )
    return _fit(lines)


def answer(
    path: str | None = None,
    files=None,
    limit: int = 50,
    *,
    session_id: str | None = None,
) -> dict:
    """Every entry point and the sinks it reaches, or only those touching ``files``."""
    from ..config import load_config

    repo = local.root(path)
    named = local.file_list(files)
    rel = {p for p in local.seeds(named, repo) if p} if named else None
    state = load_config().db_path.parent / "hook-sessions"
    sym = hook_common.symbols(repo, state, session_id)
    extra = surface.db_sinks(sym, table_map.read(repo, state).uses) if table_map.enabled() else []
    data = surface.build(sym, extra, skip=is_test)
    rows = data["entries"]
    if rel is not None:
        rows = [(e, f) for e, f in rows if e.path in rel or any(r.path in rel for r in f)]
    limit = min(max(0, limit), MAX_LIMIT)
    out = {
        "entries": [_entry(e, f) for e, f in rows[:limit]],
        "unreached": [
            {"sink": n, "category": surface.category(n), "path": _safe(p), "symbol": label(q)}
            | {"line": ln}
            for p, q, n, ln in data["unreached"][:limit]
            if rel is None or p in rel
        ],
        "totals": {
            "entries": len(rows),
            "reaching": sum(bool(f) for _, f in rows),
            "unresolved": sum(not (e.nodes or e.inline) for e, _ in rows),
            "sinks": data["sinks"],
            "unreached": len(data["unreached"]),
        },
    }
    return {"ok": True, **out, "text": render(out, repo.name), "caveat": CAVEAT}
