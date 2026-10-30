"""The note after an edit: who calls what the edit touched, and the tests nearest it.

Called from ``gingugu hook edit`` (PostToolUse) after an ``Edit``, ``Write`` or
``MultiEdit`` of a Python file. The edit tripwire denies only a *blind* edit, so
this is where an informed one learns what it reached: for each definition the
new text landed in, the callers in other files and the test files nearest them.
A definition with no cross-file caller says nothing, and each is named once per
session.

It answers from the caches the Read/Grep hooks and the tripwire already built -
the file on disk has just changed, so a fingerprint-checked lookup would rebuild
the whole graph for a number that is only stale in the file just edited. Call
counts are a lower bound: the symbol graph never guesses.

Like every hook it must never break a session: any failure returns None.
"""

from __future__ import annotations

from pathlib import Path

from . import edit_hook, edit_symbols, hook_common, ledger, rank, symbols
from .hook_common import _claim, _show
from .lang.python import is_test
from .scan import XrayError

HEADER = "GINGUGU X-RAY after edit (calls, a lower bound):"
MAX_DEFS = 3
_NAMED = 3
_MAX_CHARS = 8800


def _news(payload: dict) -> list[str] | None:
    """The new strings of an Edit or MultiEdit; None for a Write or anything else."""
    tool_input = payload.get("tool_input", {})
    if payload.get("tool_name") == "Edit":
        found = [tool_input.get("new_string")]
    elif payload.get("tool_name") == "MultiEdit" and isinstance(tool_input.get("edits"), list):
        found = [e.get("new_string") for e in tool_input["edits"] if isinstance(e, dict)]
    else:
        return None
    return [n for n in found if isinstance(n, str) and n]


def _quals(sym: symbols.Symbols, rel: str, text: str, news: list[str]) -> list[str]:
    """The definitions the new strings now sit in, first seen first, each once."""
    out: list[str] = []
    text = text.replace("\r\n", "\n")  # line endings are compared, not their bytes
    for new in (n.replace("\r\n", "\n") for n in news):
        i = text.find(new)
        if i < 0:
            continue
        start = text.count("\n", 0, i) + 1
        end = start + new.rstrip("\n").count("\n")
        out += [q for q in symbols.enclosing(sym, rel, start, end) if q not in out]
    return out


def _tests(found: dict) -> list[str]:
    """Distinct test files among everything that reaches the definition, nearest first."""
    nearest: dict[str, int] = {}
    for row in found["reach"]:
        if is_test(row["path"]):
            nearest[row["path"]] = min(row["hops"], nearest.get(row["path"], row["hops"]))
    return sorted(nearest, key=lambda p: (nearest[p], p))


def _line(qual: str, found: dict, rel: str) -> tuple[str, list[str]] | None:
    """(the definition's line, its tests), or None without a cross-file caller."""
    others = [d for d in found["direct"] if d["path"] != rel]
    if not others:
        return None
    files = {d["path"] for d in others}
    n = len(others)
    named = ", ".join(f"{_show(d['path'])}:{symbols.label(d['symbol'])}" for d in others[:_NAMED])
    more = f" (+{n - _NAMED} more)" if n > _NAMED else ""
    text = (
        f"`{symbols.label(qual)}` (L{found['line']}): {n} caller{'' if n == 1 else 's'} "
        f"in {len(files)} other file{'' if len(files) == 1 else 's'} - {named}{more}."
    )
    tests = _tests(found)
    if tests:
        text += f" Tests: {hook_common.named(tests, _NAMED)}."
    return text, tests


def _read(root: Path, rel: str) -> str | None:
    with open(root / rel, "rb") as handle:
        data = handle.read(edit_symbols.MAX_READ_BYTES + 1)
    if len(data) > edit_symbols.MAX_READ_BYTES:
        return None
    return data.decode("utf-8", errors="replace")


def _import_tests(root: Path, rel: str, state_dir: Path) -> list[str]:
    """The tests nearest ``rel`` by import, from the graph cached before the edit."""
    g = hook_common.cached_graph(root, state_dir)
    if g is None or rel not in g.files or rel in g.packages:
        return []
    blast = edit_hook._without_seed(
        rank.blast([rel], g.files, g.links, g.ranks, hidden=g.packages), rel
    )
    return hook_common.nearest_tests(blast["tests"])


def _lines(root: Path, rel: str, state_dir: Path, session_id: str, news: list[str]) -> list[str]:
    text = _read(root, rel)
    if text is None:
        return []
    sym = hook_common.cached_symbols(root, state_dir)
    if sym is None:
        sym = hook_common.symbols(root, state_dir, session_id)
    out: list[str] = []
    for q in _quals(sym, rel, text, news):
        if len(out) >= MAX_DEFS:
            break
        found = symbols.callers(sym, rel, q)
        line = _line(q, found, rel) if found is not None else None
        if line is None:
            continue
        # Once per definition per session: a second edit of it is already informed.
        if not _claim(
            hook_common.claim_path(state_dir, "xray-edit", session_id, root, f"{rel}\0{q}")
        ):
            continue
        out.append(line[0])
        ledger.predict(state_dir, session_id, root, rel, "calls", line[1])
    if out:
        ledger.predict(
            state_dir, session_id, root, rel, "imports", _import_tests(root, rel, state_dir)
        )
    return out


def check(payload: dict, state_dir: Path) -> str | None:
    """The note for the definitions this edit landed in, else None."""
    try:
        if not edit_hook.enabled() or not isinstance(payload.get("tool_input"), dict):
            return None
        target = edit_hook._target(payload)
        session_id = payload.get("session_id")
        if target is None or not isinstance(session_id, str) or not session_id:
            return None
        root, rel = target
        if not rel.endswith(".py"):
            return None
        # The file is seen now, by whoever wrote it: the blind-edit deny stands down.
        _claim(hook_common.claim_path(state_dir, "xray-read", session_id, root, rel))
        news = _news(payload)
        if not news:
            return None
        lines = _lines(root, rel, state_dir, session_id, news)
        if not lines:
            return None
        return "\n".join([HEADER, *lines])[:_MAX_CHARS]
    except (XrayError, ValueError, OSError):
        return None
