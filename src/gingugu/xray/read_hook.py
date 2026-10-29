"""X-ray on arrival: the map of a repo and the neighbourhood of what is read.

Called from ``gingugu hook read`` (PostToolUse). Where the edit hook denies, this
only informs: its text rides ``additionalContext`` next to the tool result, so
the agent has the shape of the code while it reads rather than after it plans an
edit. The first ``Read``/``Grep``/``Glob`` in a repo brings the repo map; each
source file ``Read`` brings who imports it and what it imports, once. Both come
from the same cached graph as the edit hook and never need the brain.

Like every hook it must never break a session: bad input, a non-repo path or an
extraction failure all return None (stay silent).
"""

from __future__ import annotations

import os
from pathlib import Path

from . import hook_common, local, rank
from .hook_common import Graph, _claim, _show, _sweep
from .scan import XrayError

READ_TOOLS = {"Read", "Grep", "Glob"}
MAP_FILES = 8
MAP_ENTRIES = 5
NAMED = 3
# Claude Code caps additionalContext at 10,000 characters.
_MAX_CHARS = 8800


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_MAP", "on").lower() not in ("0", "off", "false")


def _search_root(payload: dict, tool_input: dict) -> Path | None:
    """The checkout a Grep/Glob searches: its ``path`` (a file means its folder)
    or, absent that, the working directory."""
    cwd = payload.get("cwd")
    base = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
    raw = tool_input.get("path")
    where = base
    if isinstance(raw, str) and raw:
        where = Path(raw) if Path(raw).is_absolute() else base / raw
        if where.is_file():
            where = where.parent
        if not where.is_dir():
            where = base
    try:
        return local.root(str(where))
    except (XrayError, ValueError, OSError):
        return None


def _importers(g: Graph) -> dict[str, set[str]]:
    """dst -> who imports it, counted like ``store.overview``: asked for, not
    implied, and a package reaching its own files is membership, not an import."""
    out: dict[str, set[str]] = {}
    for ln in g.links:
        if not ln.implicit and ln.src not in g.packages and ln.src != ln.dst:
            out.setdefault(ln.dst, set()).add(ln.src)
    return out


def _ranked(g: Graph) -> list[str]:
    return sorted((p for p in g.files if p not in g.packages), key=lambda p: (-g.ranks[p], p))


def _render_map(root: Path, g: Graph) -> str | None:
    source = _ranked(g)
    if not source:
        return None  # nothing X-ray can parse: no map is better than an empty one
    counts: dict[str, int] = {}
    for p in source:
        language = g.langs.get(p, "unknown")
        counts[language] = counts.get(language, 0) + 1
    langs = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    imported_by = _importers(g)
    edges = sum(len(v) for v in imported_by.values())
    tests = sum(1 for p in source if g.files[p])
    lines = [
        f"GINGUGU X-RAY MAP: {_show(root.name)} - {len(source)} files ({langs}), "
        f"{edges} import edges, {tests} tests. Top by rank:"
    ]
    for p in source[:MAP_FILES]:
        n = len(imported_by.get(p, ()))
        lines.append(f"- {_show(p)} ({n} importer{'' if n == 1 else 's'})")
    entries = sorted(e for e in g.entries if e not in g.packages)
    if entries:
        named = ", ".join(_show(e) for e in entries[:MAP_ENTRIES])
        more = f" (+{len(entries) - MAP_ENTRIES} more)" if len(entries) > MAP_ENTRIES else ""
        lines.append(f"Entry points: {named}{more}")
    lines.append(
        "More: code_xray overview (ranked files), code_xray blast (what a change reaches)."
    )
    return "\n".join(lines)


def _names(paths: list[str]) -> str:
    listed = ", ".join(_show(p) for p in paths[:NAMED])
    return listed + (f" (+{len(paths) - NAMED} more)" if len(paths) > NAMED else "")


def _render_neighbourhood(rel: str, g: Graph) -> str | None:
    if rel not in g.files or rel in g.packages or rel.rsplit("/", 1)[-1] == "__init__.py":
        return None
    by_rank = sorted(_importers(g).get(rel, ()), key=lambda p: (-g.ranks.get(p, 0), p))
    imports = sorted(
        {ln.dst for ln in g.links if ln.src == rel and not ln.implicit and ln.dst != rel},
        key=lambda p: (-g.ranks.get(p, 0), p),
    )
    if not by_rank and not imports:
        return None
    source = _ranked(g)
    position = source.index(rel) + 1 if rel in source else len(source)
    lines = [f"GINGUGU X-RAY: {_show(rel)} - rank {position} of {len(source)}."]
    if by_rank:
        lines.append(f"Imported by {len(by_rank)}: {_names(by_rank)}")
        blast = rank.blast([rel], g.files, g.links, g.ranks, hidden=g.packages)
        tests = [r for r in blast["tests"] if r["path"] != rel]
        reach = blast["code_total"] + len(tests)
        noun = "candidate test" if len(tests) == 1 else "candidate tests"
        lines.append(f"Reaches {reach} files transitively ({len(tests)} {noun}).")
    if imports:
        lines.append(f"Imports {len(imports)}: {_names(imports)}")
    return "\n".join(lines)


def check(payload: dict, state_dir: Path) -> str | None:
    """The map and/or neighbourhood to add to a read, else None."""
    try:
        tool = payload.get("tool_name")
        tool_input = payload.get("tool_input")
        session_id = payload.get("session_id")
        if tool not in READ_TOOLS or not isinstance(tool_input, dict):
            return None
        if not isinstance(session_id, str) or not session_id:
            return None  # a shared "unknown" would let one session silence another
        rel = None
        if tool == "Read":
            raw = tool_input.get("file_path")
            if not isinstance(raw, str) or not raw:
                return None
            target = hook_common.resolve_file(raw, payload.get("cwd"))
            if target is None:
                return None
            root, rel = target
        else:
            root = _search_root(payload, tool_input)
            if root is None:
                return None
        map_claim = hook_common.claim_path(state_dir, "xray-map", session_id, root)
        read_claim = hook_common.claim_path(state_dir, "xray-read", session_id, root, rel or "")
        want_map = not map_claim.exists()
        want_read = rel is not None and not read_claim.exists()
        if not (want_map or want_read):
            return None
        g = hook_common.graph(root, state_dir, session_id)
        # Claimed whether or not there is anything to say: silence is an answer
        # too, and an unclaimed silent file would cost a fingerprint every read.
        parts = []
        if want_map and _claim(map_claim):
            repo_map = _render_map(root, g)
            if repo_map:
                parts.append(repo_map)
        if want_read and rel is not None and _claim(read_claim):
            near = _render_neighbourhood(rel, g)
            if near:
                parts.append(near)
        if not parts:
            return None
        _sweep(state_dir)
        return "\n\n".join(parts)[:_MAX_CHARS]
    except (XrayError, ValueError, OSError):
        return None
