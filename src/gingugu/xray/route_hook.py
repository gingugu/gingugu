"""X7b in the hooks: routes on a file's first read, and a new orphan call or a dropped
route after an edit.

A first read of a file names the routes it serves, with who calls each, and the
calls it makes, with what serves each. An edit that adds a call served nowhere in
the repo says so; so does one that stops serving a route other files still call.
Once per route per session. Names and lines only.

The map is X7d's entry points joined to X23's route mentions. When the joined
calls from code are outnumbered by the unserved ones, X7d is missing a framework
here, and the hooks never say 'served nowhere'.

Like every hook it must never break a session: any failure returns nothing.
"""

from __future__ import annotations

from pathlib import Path

from . import hook_common, route_join, route_map
from .env_hook import _new_text
from .hook_common import _claim, _show
from .links import mentions, model
from .route_show import caller_key, display, groups_of, line_of

SHOWN = 4  # items in a read-hook line
LOCS = 2  # locations per item
MAX_EDIT = 3
BUDGET_S = 1.0  # a hook's route scan; the cache keeps what it reached


def _more(shown: list[str], total: int) -> str:
    return ", ".join(shown) + (f" +{total - LOCS}" if total > LOCS else "")


def _callers(pairs) -> list[tuple[str, int]]:
    return sorted(set(pairs), key=lambda pl: caller_key(*pl))


def trusted(m: route_map.RouteMap, exclude=()) -> bool:
    """True when the map is thick enough to say 'served nowhere': at least one
    joined call from code, and no more than one unserved per two joined."""
    skip = set(exclude)
    unserved = [
        c for c in m.unserved if route_join.caller_class(c.path) == "code" and c not in skip
    ]
    joined = {e.call for e in m.edges if route_join.caller_class(e.call.path) == "code"}
    return len(joined) >= 1 and 2 * len(unserved) <= len(joined)


def _serves(m: route_map.RouteMap, rel: str) -> list[str]:
    mine = groups_of(s for s in m.serves if s.path == rel)
    edges = route_map.served_in(m, rel)
    out = []
    for k, group in sorted(mine.items(), key=lambda kv: line_of(kv[1])):
        found = _callers(
            (e.call.path, e.call.line)
            for e in edges
            if (e.serve.path, e.serve.head, e.serve.key) == k
        )
        route = _show(display(group))
        if found:
            locs = _more([f"{_show(p)}:{n}" for p, n in found[:LOCS]], len(found))
            out.append(f"{route} (called by {locs})")
        else:
            out.append(f"{route} (no caller here)")
    return out


def _calls(m: route_map.RouteMap, rel: str) -> list[str]:
    rows: list[tuple[int, str]] = []
    by_key: dict[str, list] = {}
    for e in route_map.calls_in(m, rel):
        by_key.setdefault(e.call.key, []).append(e)
    for key, edges in by_key.items():
        served = list(dict.fromkeys((e.serve.path, e.serve.line) for e in edges))
        locs = _more([f"{_show(p)}:{n}" for p, n in served[:LOCS]], len(served))
        rows.append((min(e.call.line for e in edges), f"{_show(key)} (served by {locs})"))
    if trusted(m):
        seen = set()
        for u in m.unserved:
            if u.path == rel and u.key not in seen:
                seen.add(u.key)
                rows.append((u.line, f"{_show(u.key)} (served nowhere here)"))
    return [text for _, text in sorted(rows, key=lambda r: r[0])]


def file_line(
    root: Path, rel: str, state_dir: Path, session_id: str | None = None, fp: str | None = None
) -> str | None:
    """``Routes: serves ...; calls ...`` for ``rel``, else None."""
    try:
        if not route_map.enabled():
            return None
        sym = hook_common.symbols(root, state_dir, session_id, fp)
        m = route_map.read(root, state_dir, budget_s=BUDGET_S, sym=sym)
        if not m.complete:
            return None  # a partial map would call a served route 'served nowhere'
        serves, calls = _serves(m, rel), _calls(m, rel)
        total = len(serves) + len(calls)
        if not total:
            return None
        serves_shown = serves[:SHOWN]
        calls_shown = calls[: SHOWN - len(serves_shown)]
        groups = [
            f"{verb} {', '.join(items)}"
            for verb, items in (("serves", serves_shown), ("calls", calls_shown))
            if items
        ]
        hidden = total - len(serves_shown) - len(calls_shown)
        more = f" (+{hidden} more; code_xray routes has them all)" if hidden else ""
        return f"Routes: {'; '.join(groups)}{more}."
    except Exception:  # noqa: BLE001 - a hook never raises
        return None


def before(root: Path, state_dir: Path):
    """The symbol graph as it stands before the edit refreshes anything, else None."""
    try:
        if not route_map.enabled():
            return None
        return hook_common.cached_symbols(root, state_dir)
    except Exception:  # noqa: BLE001 - a hook never raises
        return None


def _added(new: list[str], old: list[str], rel: str) -> list[tuple[str, str]]:
    """(route key, host) the new text mentions and the old text did not."""

    def keys(texts: list[str]) -> dict[str, str]:
        found = mentions.extract({rel: "\n".join(texts)}) if texts else []
        return {f.key: f.detail for f in found if f.kind == model.ROUTE}

    known = keys(old)
    return [(k, h) for k, h in keys(new).items() if k not in known]


def _say(todo: list[tuple[str, str, str]], state_dir: Path, session_id: str, root: Path):
    """The lines not yet said this session, at most MAX_EDIT; a line past the cap
    is never claimed, so a later edit can still say it."""
    out = []
    for kind, route, line in todo:
        claim = hook_common.claim_path(
            state_dir, "xray-routes-edit", session_id, root, f"{kind}:{route}"
        )
        if _claim(claim):
            out.append(line)
            if len(out) == MAX_EDIT:
                break
    return out


def _new_calls(m: route_map.RouteMap, rel: str, added) -> list[tuple[str, str, str]]:
    if route_join.caller_class(rel) != "code":
        return []  # a test probing a 404 is not a typo
    out = []
    for key, _host in added:
        mine = [c for c in m.calls if c.path == rel and c.key == key]
        if not mine or not any(c in m.unserved for c in mine):
            continue
        if not trusted(m, exclude=mine):
            continue
        text = (
            f"- Routes: calls {_show(key)}, served nowhere here "
            "(a typo, or a route not built yet?)."
        )
        out.append(("calls", key, text))
    return out


def _dropped(m: route_map.RouteMap, rel: str, before_sym) -> list[tuple[str, str, str]]:
    old = groups_of(s for s in route_map.serves_of(before_sym) if s.path == rel)
    new = groups_of(s for s in m.serves if s.path == rel)
    out = []
    for k, group in old.items():
        if k in new:
            continue
        edges = [e for e in route_join.join(group, m.calls) if e.call.path != rel]
        found = _callers((e.call.path, e.call.line) for e in edges)
        if not found:
            continue
        route = _show(display(group))
        locs = ", ".join(f"{_show(p)}:{n}" for p, n in found[:LOCS])
        if len(found) > LOCS:
            locs += f" (+{len(found) - LOCS} more)"
        out.append(("gone", route, f"- Routes: no longer serves {route} - still called by {locs}."))
    return out


def edit_lines(
    payload: dict, root: Path, rel: str, state_dir: Path, session_id: str, before_sym=None
) -> list[str]:
    """Lines for a call this edit adds that nothing serves, and a route it stops
    serving that other files still call."""
    try:
        if not route_map.enabled():
            return []
        new, old = _new_text(payload)
        added = _added(new, old, rel)
        gone = before_sym is not None and any(
            s.path == rel for s in route_map.serves_of(before_sym)
        )
        if not added and not gone:
            return []
        m = route_map.read(root, state_dir, budget_s=BUDGET_S, session_id=session_id)
        if not m.complete:
            return []  # 'served nowhere' and 'still called' need every file read
        todo = _new_calls(m, rel, added)
        if gone:
            todo += _dropped(m, rel, before_sym)
        return _say(todo, state_dir, session_id, root)
    except Exception:  # noqa: BLE001 - a hook never raises
        return []
