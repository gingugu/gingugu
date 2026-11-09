"""X7a in the hooks: env vars on a file's first read, and a new unset read after an edit.

A first read of a file names the env vars it reads, with where each is set, and
the ones it sets, with who reads them. An edit that starts reading a name set
nowhere in the repo says so, once per name per session. Names and lines only.

Like every hook it must never break a session: any failure returns nothing.
"""

from __future__ import annotations

from pathlib import Path

from . import env_map, hook_common
from .hook_common import _claim, _show

SHOWN = 4  # names in a read-hook line
LOCS = 2  # locations per name
MAX_EDIT = 3


def _is_test(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return (
        path.startswith(("tests/", "test/"))
        or "/tests/" in path
        or base.startswith("test_")
        or base.endswith(("_test.go", "_test.py"))
        or ".test." in base
        or ".spec." in base
    )


def _locs(found: list[tuple[str, int]]) -> str:
    found = sorted(found, key=lambda fl: (_is_test(fl[0]), fl[0], fl[1]))  # config before tests
    shown = ", ".join(f"{_show(f)}:{n}" for f, n in found[:LOCS])
    return shown + (f" +{len(found) - LOCS}" if len(found) > LOCS else "")


def _part(name: str, other: list[tuple[str, int]], verb: str) -> str:
    where = _locs(other) if other else None
    return f"{name} ({verb} {where})" if where else f"{name} ({verb.split()[0]} nowhere here)"


def file_line(root: Path, rel: str, state_dir: Path) -> str | None:
    """``Env: reads ...; sets ...`` for ``rel``, else None."""
    try:
        if not env_map.enabled() or env_map.kind(rel) is None:
            return None  # a file no reader handles names nothing: skip the repo scan
        m = env_map.read(root, state_dir)
        reads = [_part(n, m.sets.get(n, []), "set in") for n in env_map.names_in(m.reads, rel)]
        sets = [_part(n, m.reads.get(n, []), "read by") for n in env_map.names_in(m.sets, rel)]
        total = len(reads) + len(sets)
        if not total:
            return None
        reads_shown = reads[:SHOWN]
        sets_shown = sets[: SHOWN - len(reads_shown)]
        groups = [
            f"{verb} {', '.join(items)}"
            for verb, items in (("reads", reads_shown), ("sets", sets_shown))
            if items
        ]
        hidden = total - len(reads_shown) - len(sets_shown)
        more = f" (+{hidden} more; code_xray env has them all)" if hidden else ""
        return f"Env: {'; '.join(groups)}{more}."
    except Exception:  # noqa: BLE001 - a hook never raises
        return None


def _new_text(payload: dict) -> tuple[list[str], list[str]]:
    """(new strings, old strings) of an Edit, MultiEdit or Write."""
    tool_input = payload.get("tool_input") or {}
    tool = payload.get("tool_name")
    if tool in ("Write", "Edit"):
        items = [tool_input]
    elif tool == "MultiEdit" and isinstance(tool_input.get("edits"), list):
        items = [e for e in tool_input["edits"] if isinstance(e, dict)]
    else:
        return [], []
    key = "content" if tool == "Write" else "new_string"
    new = [i[key] for i in items if isinstance(i.get(key), str)]
    old = [i["old_string"] for i in items if isinstance(i.get("old_string"), str)]
    return new, old


def edit_lines(payload: dict, root: Path, rel: str, state_dir: Path, session_id: str) -> list[str]:
    """Lines for names this edit reads that are set nowhere in the repo."""
    try:
        if not env_map.enabled():
            return []
        new, old = _new_text(payload)
        added = [n for text in new for n, _ in env_map.scan_text(rel, text)[0]]
        known = {n for text in old for n, _ in env_map.scan_text(rel, text)[0]}
        fresh = [n for n in dict.fromkeys(added) if n not in known]
        if not fresh:
            return []
        m = env_map.read(root, state_dir)
        out: list[str] = []
        for name in fresh:
            if name in m.sets:
                continue
            claim = hook_common.claim_path(state_dir, "xray-env-edit", session_id, root, name)
            if not _claim(claim):
                continue
            out.append(
                f"- Env: reads {name}, set nowhere in this repo "
                "(deploy config, CI secret or shell?)."
            )
            if len(out) == MAX_EDIT:
                break
        return out
    except Exception:  # noqa: BLE001 - a hook never raises
        return []
