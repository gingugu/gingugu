"""``code_xray`` action ``env`` (X7a), answered locally.

Which env vars a file reads and where each is set; which it sets and who reads
them; or, with no files, every name with the one-sided ones first. Built where
the checkout is, so the brain is never asked and never sees a path. Shared by
the stdio handler, the proxy and ``gingugu xray``, like ``docs_tool``.
"""

from __future__ import annotations

from . import env_map, local
from .brief import _safe
from .hook_common import _show

ACTIONS = ("env",)
MAX_LIMIT = 200
MAX_CHARS = 8800
MAX_LOCS = 10
SHOWN = 3
CAVEAT = (
    "Only literal names in known read and set forms are seen; a name built at run time "
    "is missed. 'Set nowhere here' can still mean set by the deploy, a CI secret or the "
    "shell. Real dotenv files are never read; only .env.example-style templates are. "
    "Names and line numbers only, never a value."
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


def _unread(data: dict) -> list[str]:
    files = data.get("unread") or []
    if not files:
        return []
    shown = ", ".join(_show(p) for p in files)
    return [f"Not read (too big, or past the name cap), so 'nowhere' is not claimed: {shown}"]


def render(data: dict, name: str) -> str:
    """Compact text for an agent; every path is quoted by ``_show``."""
    if "files" in data:
        none = "no file read" if data.get("unread") else "nowhere here"
        lines = [f"GINGUGU X-RAY ENV: {_show(name)} - env vars of {len(data['files'])} named files"]
        for f in data["files"]:
            lines.append(f"- {_show(f['path'])}" + ("" if f["reads"] or f["sets"] else ": none"))
            for r in f["reads"]:
                lines.append(f"  reads {r['name']}: set in {_where(r['set_in'], none)}")
            for s in f["sets"]:
                lines.append(f"  sets {s['name']}: read by {_where(s['read_by'], none)}")
        return _fit(lines + _unread(data))
    t = data["totals"]
    lines = [
        f"GINGUGU X-RAY ENV: {_show(name)} - {t['names']} names, {t['never_set']} read but "
        f"never set here, {t['never_read']} set but never read here. One-sided first:",
        *_unread(data),
    ]
    for r in data["names"]:
        flag = f" - {r['flag']}" if r["flag"] else ""
        lines.append(f"- {r['name']}: read in {r['reads']}, set in {r['sets']}{flag}")
    return _fit(lines)


def _locs(found: list[tuple[str, int]]) -> list[dict]:
    return [{"path": _safe(p), "line": n} for p, n in found[:MAX_LOCS]]


def _file(m: env_map.EnvMap, rel: str) -> dict:
    return {
        "path": _safe(rel),
        "reads": [
            {"name": n, "set_in": _locs(m.sets.get(n, []))} for n in env_map.names_in(m.reads, rel)
        ],
        "sets": [
            {"name": n, "read_by": _locs(m.reads.get(n, []))} for n in env_map.names_in(m.sets, rel)
        ],
    }


def _flag(reads: int, sets: int, unread: bool = False) -> str | None:
    # A file too big to read may hold the other side: never say 'never ... here' then.
    if not sets:
        return "not set in any file read" if unread else "never set here"
    if reads:
        return None
    return "not read in any file read" if unread else "never read here"


def _names(m: env_map.EnvMap, limit: int) -> dict:
    rows = [
        {
            "name": n,
            "reads": len(m.reads.get(n, [])),
            "sets": len(m.sets.get(n, [])),
            "flag": _flag(len(m.reads.get(n, [])), len(m.sets.get(n, [])), bool(m.unread)),
        }
        for n in sorted(set(m.reads) | set(m.sets))
    ]
    order = {
        "never set here": 0,
        "not set in any file read": 0,
        "never read here": 1,
        "not read in any file read": 1,
        None: 2,
    }
    rows.sort(key=lambda r: (order[r["flag"]], r["name"]))
    return {
        "names": rows[: min(max(0, limit), MAX_LIMIT)],
        "totals": {
            "names": len(rows),
            "never_set": sum(r["flag"] == "never set here" for r in rows),
            "never_read": sum(r["flag"] == "never read here" for r in rows),
        },
    }


def answer(
    path: str | None = None,
    files=None,
    limit: int = 50,
    *,
    session_id: str | None = None,
) -> dict:
    """The env vars the named files read and set, or (no files) every name."""
    from ..config import load_config

    if not env_map.enabled():
        raise ValueError("the env map is off (MEMORY_XRAY_ENV)")
    repo = local.root(path)
    named = local.file_list(files)
    rel = [p for p in local.seeds(named, repo) if p] if named else None
    m = env_map.read(repo, load_config().db_path.parent / "hook-sessions")
    data = {"files": [_file(m, p) for p in rel]} if rel else _names(m, limit)
    data["unread"] = [_safe(p) for p in m.unread[:MAX_LOCS]]
    return {"ok": True, **data, "text": render(data, repo.name), "caveat": CAVEAT}
