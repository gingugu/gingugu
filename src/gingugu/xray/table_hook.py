"""X7c in the hooks: tables on a file's first read, and a new orphan use or a risky
change to a definition after an edit.

A first read of a file names the tables it uses, with where each is defined (or
dropped), and the ones it defines, with who else uses them. An edit that starts
using a table defined nowhere in the repo, or dropped there, says so; so does one
that changes a definition other files use, or drops a table other files still use.
Once per name per session. Names and lines only.

Like every hook it must never break a session: any failure returns nothing.
"""

from __future__ import annotations

from pathlib import Path

from . import hook_common, table_map
from .env_hook import _is_test, _new_text
from .hook_common import _claim, _show

SHOWN = 4  # names in a read-hook line
LOCS = 2  # locations per name
MAX_EDIT = 3
BUDGET_S = 1.0  # a hook's table scan; the cache keeps what it reached


def _more(shown: list[str], total: int) -> str:
    return ", ".join(shown) + (f" +{total - LOCS}" if total > LOCS else "")


def _files(paths: list[str]) -> str:
    """Distinct files, code before tests, the first few named."""
    distinct = sorted(set(paths), key=lambda p: (_is_test(p), p))
    return _more([_show(p) for p in distinct[:LOCS]], len(distinct))


def _locs(found: list[tuple[str, int]]) -> str:
    return _more([f"{_show(f)}:{n}" for f, n in found[:LOCS]], len(found))


def _others(refs: table_map.Refs, name: str, rel: str) -> list[str]:
    return [f for f, _ in refs.get(name, []) if f != rel]


def file_line(root: Path, rel: str, state_dir: Path) -> str | None:
    """``Tables: defines ...; uses ...`` for ``rel``, else None."""
    try:
        if not table_map.enabled() or table_map.kind(rel) is None:
            return None  # a file no reader handles names nothing: skip the repo scan
        m = table_map.read(root, state_dir, budget_s=BUDGET_S)
        if not m.complete:
            return None  # a partial map would call a defined table 'defined nowhere'
        own = table_map.names_in(m.defines, rel)
        defines = []
        for n in own:
            users = _others(m.uses, n, rel)
            defines.append(
                f"{n} (used by {_files(users)})" if users else f"{n} (used nowhere else here)"
            )
        uses = []
        for n in table_map.names_in(m.uses, rel):
            if n in own:
                continue  # its own tables are not news
            gone = table_map.dropped(m, n)
            if gone:
                uses.append(f"{n} (dropped in {_locs(gone)})")
            elif m.defines.get(n):
                uses.append(f"{n} (defined in {_locs(m.defines[n])})")
            elif m.unread:  # a file too big to read may define it: never say 'nowhere'
                verb = "is" if len(m.unread) == 1 else "are"
                uses.append(f"{n} (not in the files read; {_files(list(m.unread))} {verb} too big)")
            else:
                uses.append(f"{n} (defined nowhere here)")
        total = len(defines) + len(uses)
        if not total:
            return None
        defines_shown = defines[:SHOWN]
        uses_shown = uses[: SHOWN - len(defines_shown)]
        groups = [
            f"{verb} {', '.join(items)}"
            for verb, items in (("defines", defines_shown), ("uses", uses_shown))
            if items
        ]
        hidden = total - len(defines_shown) - len(uses_shown)
        more = f" (+{hidden} more; code_xray tables has them all)" if hidden else ""
        return f"Tables: {'; '.join(groups)}{more}."
    except Exception:  # noqa: BLE001 - a hook never raises
        return None


def _scans(texts: list[str], rel: str) -> list[tuple]:
    """Each text scanned once; one past the map's file cap is not scanned at all."""
    return [table_map.scan_text(rel, t) for t in texts if len(t) <= table_map.MAX_FILE_BYTES]


def _names(scans: list[tuple], which: int) -> list[str]:
    """Distinct names, in order, from ``scans`` (0 defines, 1 drops, 2 uses)."""
    return list(dict.fromkeys(n for s in scans for n, _ in s[which]))


def _candidates(new: list[str], old: list[str], rel: str) -> list[tuple[str, str]]:
    """(kind, name) the edit may need to speak of, in the order they are said."""
    new_s, old_s = _scans(new, rel), _scans(old, rel)
    known = set(_names(old_s, 2))
    out = [("uses", n) for n in _names(new_s, 2) if n not in known]
    out += [("defines", n) for n in _names(new_s + old_s, 0)]
    out += [("drops", n) for n in _names(new_s, 1)]
    return out


def _say(m: table_map.TableMap, kind: str, name: str, rel: str) -> str | None:
    if kind == "uses":
        if not m.defines:
            return None  # no schema in this repo: it lives elsewhere
        gone = table_map.dropped(m, name)
        if gone:
            return f"- Tables: uses {name}, dropped in {_locs(gone[:1])}."
        if name not in m.defines and not m.unread:  # an unread file may define it
            return (
                f"- Tables: uses {name}, defined nowhere in this repo "
                "(a typo, or another service's table?)."
            )
        return None
    users = _others(m.uses, name, rel)
    if not users:
        return None
    if kind == "defines":
        count = len(set(users))
        return (
            f"- Tables: defines {name}, used by {count} other files (code_xray tables lists them)."
        )
    if name in table_map.names_in(m.defines, rel):
        return None  # dropped and recreated in this file: a rebuild
    return f"- Tables: drops {name}, still used by {_files(users)}."


def edit_lines(payload: dict, root: Path, rel: str, state_dir: Path, session_id: str) -> list[str]:
    """Lines for tables this edit uses unknown, redefines or drops under other files' feet."""
    try:
        if not table_map.enabled():
            return []
        new, old = _new_text(payload)
        todo = _candidates(new, old, rel)
        if not todo:
            return []
        m = table_map.read(root, state_dir, budget_s=BUDGET_S)
        if not m.complete:
            return []  # 'defined nowhere' needs every file read
        out: list[str] = []
        for kind, name in todo:
            line = _say(m, kind, name, rel)
            if line is None:
                continue
            claim = hook_common.claim_path(
                state_dir, "xray-tables-edit", session_id, root, f"{kind}:{name}"
            )
            if not _claim(claim):
                continue
            out.append(line)
            if len(out) == MAX_EDIT:
                break
        return out
    except Exception:  # noqa: BLE001 - a hook never raises
        return []
