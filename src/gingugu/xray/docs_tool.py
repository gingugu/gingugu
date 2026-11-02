"""``code_xray`` action ``docs`` (X22), answered locally.

Which docs cite a file, in this repo and in sibling checkouts on this machine.
Built where the checkout is, so the brain is never asked and never sees a path.
Shared by the stdio handler, the proxy and ``gingugu xray``, like ``history_tool``.
"""

from __future__ import annotations

from . import docs_map, local
from .hook_common import _show

ACTIONS = ("docs",)
MAX_LIMIT = 100
MAX_CHARS = 8800
MAX_SHOWN = 5
CAVEAT = (
    "A citation is a path mentioned in a doc that resolves to exactly one file of this "
    "repo; an ambiguous name cites nothing. Sibling checkouts' docs count only "
    "repo-qualified paths and GitHub links. A citation is a candidate to re-read, not "
    "proof the doc is wrong."
)


def _fit(lines: list[str]) -> str:
    text = "\n".join(lines)
    if len(text) <= MAX_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_CHARS)  # whole lines, never one cut mid-way
    return text[: cut if cut > 0 else MAX_CHARS]


def _docs(rows: list[dict]) -> str:
    shown = [_show(d["doc"] if d["repo"] is None else f"{d['repo']}:{d['doc']}") for d in rows]
    more = f" (+{len(shown) - MAX_SHOWN} more)" if len(shown) > MAX_SHOWN else ""
    return ", ".join(shown[:MAX_SHOWN]) + more


def render(data: dict, name: str) -> str:
    """Compact text for an agent; every path is quoted by ``_show``."""
    if "files" in data:
        lines = [
            f"GINGUGU X-RAY DOCS: {_show(name)} - docs citing {len(data['files'])} named files"
        ]
        lines += [
            f"- {_show(r['path'])}: " + (_docs(r["docs"]) if r["docs"] else "no doc cites it")
            for r in data["files"]
        ]
        return _fit(lines)
    t = data["totals"]
    lines = [
        f"GINGUGU X-RAY DOCS: {_show(name)} - {t['cited_files']} files cited by "
        f"{t['docs']} docs ({t['siblings']} sibling checkouts read). Most cited first:"
    ]
    lines += [
        f"- {_show(r['path'])}: {r['docs']} doc{'' if r['docs'] == 1 else 's'}"
        for r in data["cited"]
    ]
    return _fit(lines)


def _rows(found: list[docs_map.Cite]) -> list[dict]:
    return [{"doc": c.doc, "repo": c.repo} for c in found]


def answer(
    path: str | None = None,
    files=None,
    limit: int = 20,
    *,
    session_id: str | None = None,
) -> dict:
    """The docs citing the named files, or (no files) the most cited files."""
    from ..config import load_config

    if not docs_map.enabled():
        raise ValueError("the docs map is off (MEMORY_XRAY_DOCS)")
    repo = local.root(path)
    named = local.file_list(files)
    rel = [p for p in local.seeds(named, repo) if p] if named else None
    m = docs_map.read(repo, load_config().db_path.parent / "hook-sessions")
    if rel:
        data = {
            "files": [{"path": p, "docs": _rows(docs_map.citing(m, p))} for p in rel],
        }
    else:
        every = docs_map.cited(m)
        ranked = sorted(every.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        n = min(max(0, limit), MAX_LIMIT)
        data = {
            "cited": [{"path": p, "docs": len(c)} for p, c in ranked[:n]],
            "totals": {
                "docs": len(m.local),
                "cited_files": len(every),
                "siblings": len(m.siblings),
            },
        }
    return {"ok": True, **data, "text": render(data, repo.name), "caveat": CAVEAT}
