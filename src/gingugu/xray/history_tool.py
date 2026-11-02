"""``code_xray`` actions ``history`` (X4) and ``blindspots`` (X6), answered locally.

Built where the checkout is, from the hook graph cache and the history cache,
so the brain is never asked and never sees a path. Shared by the stdio handler,
the proxy and ``gingugu xray``, like ``brief.answer``.
"""

from __future__ import annotations

from . import history, hook_common, hotspots, local
from .hook_common import _show

ACTIONS = ("history", "blindspots")
MAX_LIMIT = 100
MAX_CHARS = 8800
CAVEAT = (
    "History is the last {window} non-merge commits. Coupling is code-maat's logical "
    "coupling (both files in >= 5 commits, together in >= 5, degree >= 30%); 'hidden' "
    "means neither imports the other. 'Tested' means a test imports the file or is "
    "named for it, a lower bound of real coverage. Candidates to inspect, not verdicts."
)


def _window(h: history.History) -> dict:
    return {"commits": h.commits, "bulk": h.bulk, "limit": history.WINDOW}


def _pct(degree: float) -> str:
    return f"{round(degree * 100)}%"


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _fit(lines: list[str]) -> str:
    text = "\n".join(lines)
    if len(text) <= MAX_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_CHARS)  # whole lines, never one cut mid-way
    return text[: cut if cut > 0 else MAX_CHARS]


def render(data: dict, name: str) -> str:
    """Compact text for an agent; every path is quoted by ``_show``."""
    w = data["window"]
    if "blindspots" in data:
        lines = [
            f"GINGUGU X-RAY BLIND SPOTS: {_show(name)} - {data['total']} imported source files "
            f"no test imports or is named for ({data['untested']} untested of {data['source']}); "
            f"history: {w['commits']} commits. Reach first, fewest authors:"
        ]
        lines += [
            f"- {_show(r['path'])} (rank {r['rank']}, {_n(r['importers'], 'importer')}, "
            f"{_n(r['commits'], 'commit')}, {_n(r['authors'], 'author')})"
            for r in data["blindspots"]
        ]
        return _fit(lines)
    lines = [f"GINGUGU X-RAY HISTORY: {_show(name)} - {w['commits']} commits read"]
    if data["hotspots"]:
        lines.append("Hotspots (churn x rank x authors):")
        lines += [
            f"- {_show(r['path'])} ({_n(r['commits'], 'commit')}, "
            f"{_n(r['authors'], 'author')}, rank {r['rank']})"
            for r in data["hotspots"]
        ]
    if data["coupling"]:
        lines.append(f"Change together ({data['hidden_total']} with no import between them):")
        lines += [
            f"- {_show(r['a'])} <-> {_show(r['b'])}: {_n(r['shared'], 'commit')} together, "
            f"degree {_pct(r['degree'])}{' (no import)' if r['hidden'] else ''}"
            for r in data["coupling"]
        ]
    return _fit(lines)


def answer(
    path: str | None,
    action: str,
    files=None,
    limit: int = 20,
    *,
    session_id: str | None = None,
) -> dict:
    """Hotspots and coupling (``history``) or blind spots (``blindspots``)."""
    from ..config import load_config

    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    repo = local.root(path)
    named = local.file_list(files)
    rel = [p for p in local.seeds(named, repo) if p] if named else None
    state = load_config().db_path.parent / "hook-sessions"
    g = hook_common.graph(repo, state, session_id)
    h = history.read(repo, state)
    n = min(max(0, limit), MAX_LIMIT)
    caveat = CAVEAT.format(window=history.WINDOW)
    if action == "blindspots":
        rows = hotspots.blindspots(g, h)
        source = hotspots._source(g)
        covered = hotspots.tested(g)
        data = {
            "window": _window(h),
            "blindspots": rows[:n],
            "total": len(rows),
            "untested": sum(1 for p in source if p not in covered),
            "source": len(source),
        }
    else:
        hot = hotspots.hotspots(g, h)
        pairs = hotspots.coupling(g, h, files=rel)
        data = {
            "window": _window(h),
            "hotspots": hot[:n],
            "hotspots_total": len(hot),
            "coupling": [{**r, "degree": round(r["degree"], 3)} for r in pairs[:n]],
            "coupling_total": len(pairs),
            "hidden_total": sum(1 for r in pairs if r["hidden"]),
        }
    return {"ok": True, **data, "text": render(data, repo.name), "caveat": caveat}
