"""``code_xray`` action ``tables`` (X7c), answered locally.

Which tables a file uses and where each is defined; which it defines or drops and
who else uses them; or, with no files, every table with the one-sided ones first.
Built where the checkout is, so the brain is never asked and never sees a path.
Shared by the stdio handler, the proxy and ``gingugu xray``, like ``env_tool``.
"""

from __future__ import annotations

from . import local, table_map
from .brief import _safe
from .hook_common import _show

ACTIONS = ("tables",)
MAX_LIMIT = 200
MAX_CHARS = 8800
MAX_LOCS = 10
SHOWN = 3
CAVEAT = (
    "Only literal table names in known forms are seen: DDL, ORM table declarations and "
    "uppercase SQL keywords (FROM, JOIN, INTO, UPDATE). ORM use through model classes is "
    "not counted; the import graph covers that. 'Defined nowhere here' can mean the "
    "schema lives in another service. Names and line numbers only."
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


def _use_line(u: dict) -> str:
    if u["dropped_in"]:
        return f"  uses {u['name']}: dropped in {_where(u['dropped_in'], '')}"
    if not u["defined_in"]:
        return f"  uses {u['name']}: defined nowhere here"
    return f"  uses {u['name']}: defined in {_where(u['defined_in'], '')}"


def render(data: dict, name: str) -> str:
    """Compact text for an agent; every path is quoted by ``_show``."""
    if "files" in data:
        lines = [
            f"GINGUGU X-RAY TABLES: {_show(name)} - tables of {len(data['files'])} named files"
        ]
        for f in data["files"]:
            quiet = f["defines"] or f["drops"] or f["uses"]
            lines.append(f"- {_show(f['path'])}" + ("" if quiet else ": none"))
            for d in f["defines"]:
                lines.append(
                    f"  defines {d['name']}: used by {_where(d['used_by'], 'nowhere else')}"
                )
            for d in f["drops"]:
                lines.append(
                    f"  drops {d['name']}: still used by {_where(d['used_by'], 'nothing')}"
                )
            lines.extend(_use_line(u) for u in f["uses"])
        return _fit(lines)
    t = data["totals"]
    lines = [
        f"GINGUGU X-RAY TABLES: {_show(name)} - {t['tables']} tables, {t['defined_nowhere']} "
        f"used but defined nowhere here, {t['dropped_still_used']} dropped but still used, "
        f"{t['used_nowhere']} defined but used nowhere here. One-sided first:"
    ]
    for r in data["tables"]:
        flag = f" - {r['flag']}" if r["flag"] else ""
        used = f"used {r['uses']}x in {r['files']} files"
        lines.append(f"- {r['name']}: defined in {r['defines']}, {used}{flag}")
    return _fit(lines)


def _locs(found: list[tuple[str, int]]) -> list[dict]:
    return [{"path": _safe(p), "line": n} for p, n in found[:MAX_LOCS]]


def _elsewhere(found: list[tuple[str, int]], rel: str) -> list[tuple[str, int]]:
    return [(f, n) for f, n in found if f != rel]


def _file(m: table_map.TableMap, rel: str) -> dict:
    return {
        "path": _safe(rel),
        "defines": [
            {"name": n, "used_by": _locs(_elsewhere(m.uses.get(n, []), rel))}
            for n in table_map.names_in(m.defines, rel)
        ],
        "drops": [
            {"name": n, "used_by": _locs(_elsewhere(m.uses.get(n, []), rel))}
            for n in table_map.names_in(m.drops, rel)
        ],
        "uses": [
            {
                "name": n,
                "defined_in": _locs(m.defines.get(n, [])),
                "dropped_in": _locs(table_map.dropped(m, n)),
            }
            for n in table_map.names_in(m.uses, rel)
        ],
    }


def _flag(m: table_map.TableMap, name: str) -> str | None:
    defines, uses = m.defines.get(name, []), m.uses.get(name, [])
    if uses and not defines:
        return "defined nowhere here"
    if uses and table_map.dropped(m, name):
        return "dropped, still used"
    defining = {f for f, _ in defines}
    if defines and not any(f not in defining for f, _ in uses):
        return "used nowhere here"
    return None


def _tables(m: table_map.TableMap, limit: int) -> dict:
    rows = [
        {
            "name": n,
            "defines": len(m.defines.get(n, [])),
            "uses": len(m.uses.get(n, [])),
            "files": len({f for f, _ in m.uses.get(n, [])}),
            "flag": _flag(m, n),
        }
        for n in sorted(set(m.defines) | set(m.uses))
    ]
    order = {"defined nowhere here": 0, "dropped, still used": 1, "used nowhere here": 2, None: 3}
    rows.sort(key=lambda r: (order[r["flag"]], r["name"]))
    return {
        "tables": rows[: min(max(0, limit), MAX_LIMIT)],
        "totals": {
            "tables": len(rows),
            "defined_nowhere": sum(r["flag"] == "defined nowhere here" for r in rows),
            "dropped_still_used": sum(r["flag"] == "dropped, still used" for r in rows),
            "used_nowhere": sum(r["flag"] == "used nowhere here" for r in rows),
        },
    }


def answer(
    path: str | None = None,
    files=None,
    limit: int = 50,
    *,
    session_id: str | None = None,
) -> dict:
    """The tables the named files define, drop and use, or (no files) every table."""
    from ..config import load_config

    if not table_map.enabled():
        raise ValueError("the table map is off (MEMORY_XRAY_TABLES)")
    repo = local.root(path)
    named = local.file_list(files)
    rel = [p for p in local.seeds(named, repo) if p] if named else None
    m = table_map.read(repo, load_config().db_path.parent / "hook-sessions")
    data = {"files": [_file(m, p) for p in rel]} if rel else _tables(m, limit)
    return {"ok": True, **data, "text": render(data, repo.name), "caveat": CAVEAT}
