"""X7d in the hooks: the attack surface on a file's first read, and what an edit
changed about it.

A first read names the entry points the file holds, each with the most dangerous
sink it reaches, and the sinks it holds that entry points elsewhere reach. Every
category speaks - database reach included - ranked most dangerous first and
capped, with ``code_xray surface`` named for the rest.

After an edit, the map before it (the symbol and table caches as they stood) is
diffed against the map after it (a fingerprint-checked rebuild, which re-parses
only what changed): every sink an entry point can reach now and could not before
is named, and a new entry point is named with what it reaches. When the rebuild
cannot run, the note says the check was skipped rather than saying nothing. A
definition the edit landed in that already lies between an entry point and a
sink is named too. Each fact once per session.

Like every hook it must never break a session: any failure returns nothing.
"""

from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from typing import NamedTuple

from . import hook_common, surface, table_map
from . import surface_route as sr
from .hook_common import _claim, _show
from .lang import is_test
from .scan import XrayError
from .symbols import Symbols, label

SHOWN = 3  # entries or sinks named per line
MORE = "code_xray surface has them all"


def enabled() -> bool:
    return os.environ.get("MEMORY_XRAY_SURFACE", "on").lower() not in ("0", "off", "false")


def _map(sym: Symbols, uses: dict) -> dict:
    extra = surface.db_sinks(sym, uses) if table_map.enabled() else []
    return surface.build(sym, extra, skip=is_test)


def name(e) -> str:
    """``http POST /users``, ``mcp credential_get``, ``main __main__``."""
    return f"{e.kind} {sr.label(e.kind, e.method, e.route, e.label)}"


def _hops(r: surface.Reach) -> str:
    return "in the handler" if r.hops == 0 else f"{r.hops} hop{'s' if r.hops > 1 else ''}"


def _more(n: int) -> str:
    return f" (+{n} more; {MORE})" if n > 0 else ""


def _worst(e, found: list) -> str:
    r = found[0]
    rest = f", +{len(found) - 1} more sink{'s' if len(found) > 2 else ''}" if len(found) > 1 else ""
    return f"{name(e)} (L{e.line}) -> {r.category} {r.sink}, {_hops(r)}{rest}"


def read_lines(root: Path, rel: str, state_dir: Path, session_id: str, fp: str) -> list[str]:
    """The surface lines for a first read of ``rel``: its entry points, and its
    sinks that entry points in other files reach."""
    try:
        if not enabled():
            return []
        sym = hook_common.symbols(root, state_dir, session_id, fp)
        uses = table_map.read(root, state_dir, budget_s=1.0).uses if table_map.enabled() else {}
        rows = _map(sym, uses)["entries"]
        out = []
        own = [(e, f) for e, f in rows if e.path == rel and f]
        if own:
            said = "; ".join(_worst(e, f) for e, f in own[:SHOWN])
            out.append(f"Surface: entry points here: {said}{_more(len(own) - SHOWN)}.")
        hit: dict[tuple, list] = {}
        for e, found in rows:
            for r in found:
                if r.path == rel and e.path != rel:
                    hit.setdefault((r.category, r.sink, r.line, r.symbol), []).append(e)
        if hit:
            order = sorted(hit, key=lambda k: (surface.cat.CATEGORIES.index(k[0]), k[2], k[1]))
            said = "; ".join(_reached(k, hit[k]) for k in order[:SHOWN])
            out.append(
                f"Surface: sinks here reached from entry points: {said}{_more(len(order) - SHOWN)}."
            )
        return out
    except Exception:  # noqa: BLE001 - a hook never raises
        return []


def _reached(key: tuple, entries: list) -> str:
    category, sink, line, symbol = key
    distinct = list(dict.fromkeys(name(e) for e in entries))
    named = ", ".join(distinct[:2]) + (f" +{len(distinct) - 2}" if len(distinct) > 2 else "")
    n = len(distinct)
    return f"{category} {sink} (L{line}, `{label(symbol)}`) from {n} entr{'y' if n == 1 else 'ies'}: {named}"


# --- after an edit ----------------------------------------------------------------------


class Before(NamedTuple):
    sym: Symbols | None
    uses: dict


def before(root: Path, state_dir: Path) -> Before:
    """The caches as they stand before anything in the edit note refreshes them."""
    try:
        if not enabled():
            return Before(None, {})
        uses = table_map.cached_uses(root, state_dir) if table_map.enabled() else {}
        return Before(hook_common.cached_symbols(root, state_dir), uses)
    except Exception:  # noqa: BLE001 - a hook never raises
        return Before(None, {})


def _ekey(e) -> tuple:
    return (e.path, e.kind, e.label, e.handler, e.method, e.route)  # lines move; identity does not


def _pairs(rows: list) -> dict[tuple, tuple]:
    return {(_ekey(e), (r.sink, r.path, r.symbol)): (e, r) for e, found in rows for r in found}


def _reach_of(adj: dict, starts) -> set:
    seen, queue = set(starts), deque(starts)
    while queue:
        for nxt in adj.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def on_path(sym: Symbols, rows: list, node: tuple) -> tuple[list, list] | None:
    """(entries that reach ``node``, sinks ``node`` reaches) when both exist: the
    definition lies on a path from outside input to a sink. None otherwise."""
    adj = surface._adjacency(sym)
    entries = [e for e, _ in rows if node in _reach_of(adj, e.nodes)]
    if not entries:
        return None
    below = _reach_of(adj, [node])
    sinks = sorted(
        {
            (p, q, n, ln)
            for e, found in rows
            for r in found
            for p, q, n, ln in [(r.path, r.symbol, r.sink, r.line)]
            if (p, q) in below
        },
        key=lambda s: (surface.cat.CATEGORIES.index(surface.category(s[2])), s[0], s[3]),
    )
    return (entries, sinks) if sinks else None


def edit_lines(
    snap: Before, root: Path, rel: str, quals: list[str], state_dir: Path, session_id: str
) -> list[str]:
    """The surface lines after an edit of ``rel`` landing in ``quals``."""
    try:
        if not enabled() or snap.sym is None:
            return []
        try:
            after = hook_common.symbols(root, state_dir, session_id)
        except (XrayError, ValueError, OSError):
            after = None
        if after is None:
            claim = hook_common.claim_path(state_dir, "xray-surface-skip", session_id, root, rel)
            if not _claim(claim):
                return []
            return [
                "- Surface: the after-edit reach check was skipped (the graph rebuild did not finish); code_xray surface rebuilds it."
            ]
        uses = table_map.read(root, state_dir, budget_s=1.0).uses if table_map.enabled() else {}
        old_rows, new_rows = _map(snap.sym, snap.uses)["entries"], _map(after, uses)["entries"]
        out = _new_entries(old_rows, new_rows, rel, state_dir, session_id, root)
        out += _new_reach(old_rows, new_rows, state_dir, session_id, root)
        out += _paths(after, new_rows, rel, quals, state_dir, session_id, root)
        return out
    except Exception:  # noqa: BLE001 - a hook never raises
        return []


def _claimed(state_dir: Path, session_id: str, root: Path, what: str) -> bool:
    return _claim(hook_common.claim_path(state_dir, "xray-surface-edit", session_id, root, what))


def _new_entries(old_rows, new_rows, rel, state_dir, session_id, root) -> list[str]:
    known = {_ekey(e) for e, _ in old_rows}
    fresh = [(e, f) for e, f in new_rows if _ekey(e) not in known and e.path == rel]
    fresh = [
        (e, f)
        for e, f in fresh
        if _claimed(state_dir, session_id, root, "entry\0" + "\0".join(_ekey(e)))
    ]
    if not fresh:
        return []
    said = "; ".join(
        _worst(e, f) if f else f"{name(e)} (L{e.line}), reaching no sink" for e, f in fresh[:SHOWN]
    )
    return [
        f"- Surface: this edit adds entry point{'s' if len(fresh) > 1 else ''} {said}{_more(len(fresh) - SHOWN)}."
    ]


def _new_reach(old_rows, new_rows, state_dir, session_id, root) -> list[str]:
    before_pairs, known = _pairs(old_rows), {_ekey(e) for e, _ in old_rows}
    fresh = [
        (e, r)
        for k, (e, r) in _pairs(new_rows).items()
        if k not in before_pairs and _ekey(e) in known  # a new entry is said above
    ]
    fresh.sort(key=lambda er: (surface._rank(er[1]), er[0].path, er[0].line))
    fresh = [
        (e, r)
        for e, r in fresh
        if _claimed(
            state_dir,
            session_id,
            root,
            "reach\0" + "\0".join(_ekey(e)) + f"\0{r.sink}\0{r.path}\0{r.symbol}",
        )
    ]
    out = []
    for e, r in fresh[:SHOWN]:
        via = (
            ""
            if r.hops == 0
            else ": " + " > ".join(label(q) for q in r.via) + (" > ..." if r.cut else "")
        )
        out.append(
            f"- Surface: this edit makes {r.category} {r.sink} ({_show(r.path)}:{r.line}, "
            f"`{label(r.symbol)}`) reachable from {name(e)} ({_hops(r)}{via})."
        )
    if len(fresh) > SHOWN:
        out.append(f"- Surface: +{len(fresh) - SHOWN} more newly reachable sinks; {MORE}.")
    return out


def _paths(after, rows, rel, quals, state_dir, session_id, root) -> list[str]:
    out = []
    for q in quals:
        found = on_path(after, rows, (rel, q))
        if found is None or not _claimed(state_dir, session_id, root, f"path\0{rel}\0{q}"):
            continue
        entries, sinks = found
        names = list(dict.fromkeys(name(e) for e in entries))
        who = ", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")
        p, s, n, ln = sinks[0]
        rest = f" (+{len(sinks) - 1} more)" if len(sinks) > 1 else ""
        out.append(
            f"- Surface: `{label(q)}` is on a path from {who} to "
            f"{surface.category(n)} {n} ({_show(p)}:{ln}){rest}."
        )
        if len(out) == SHOWN:
            break
    return out
