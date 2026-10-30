"""The symbol lines of the edit tripwire: which definitions an edit lands in, and who calls them.

The file blast says what *imports* the file; these lines narrow it to the
definitions the edit actually touches. They are found by locating each
``old_string`` in the file as it is now, so only ``Edit`` and ``MultiEdit``
have them - a ``Write`` replaces the file and names no place.

Call counts are a lower bound (the symbol graph never guesses), and the last
line says so whenever any definition was named.
"""

from __future__ import annotations

from pathlib import Path

from . import hook_common, symbols
from .hook_common import _show

MAX_NAMED = 3
_BOUND = (
    "Call counts are a lower bound: calls through untyped values and inherited "
    "methods are not resolved."
)


def _olds(payload: dict) -> list[str]:
    """The ``old_string`` of an Edit, or of each edit in a MultiEdit."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    if payload.get("tool_name") == "Edit":
        found = [tool_input.get("old_string")]
    elif payload.get("tool_name") == "MultiEdit" and isinstance(tool_input.get("edits"), list):
        found = [e.get("old_string") for e in tool_input["edits"] if isinstance(e, dict)]
    else:
        return []
    return [o for o in found if isinstance(o, str) and o]


def _quals(sym: symbols.Symbols, rel: str, text: str, olds: list[str]) -> list[str]:
    """The definitions the old strings sit in, first seen first, each once."""
    out: list[str] = []
    for old in olds:
        i = text.find(old)
        if i < 0:
            continue
        start = text.count("\n", 0, i) + 1
        end = start + old.rstrip("\n").count("\n")
        out += [q for q in symbols.enclosing(sym, rel, start, end) if q not in out]
    return out


def _line(sym: symbols.Symbols, rel: str, qual: str) -> str | None:
    found = symbols.callers(sym, rel, qual, limit=MAX_NAMED)
    if found is None:
        return None
    head = f"Edit lands in `{qual}` (L{found['line']}-{found['end']}): "
    n = found["direct_total"]
    if not n:
        return head + "no resolved callers."
    named = ", ".join(_show(f"{d['path']}:{d['symbol']}") for d in found["direct"])
    more = f" (+{n - MAX_NAMED} more)" if n > MAX_NAMED else ""
    d, f = found["reach_total"], len(found["reach_files"])
    return (
        f"{head}{n} caller{'' if n == 1 else 's'} - {named}{more}; "
        f"reaches {d} definition{'' if d == 1 else 's'} in {f} file{'' if f == 1 else 's'}."
    )


def lines(
    payload: dict, root: Path, rel: str, state_dir: Path, session_id: str, fp: str | None = None
) -> list[str]:
    """The symbol lines for this edit; empty when it names none or cannot be read."""
    olds = _olds(payload)
    if not olds:
        return []
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    sym = hook_common.symbols(root, state_dir, session_id, fp)
    quals = _quals(sym, rel, text, olds)
    out = [ln for q in quals[:MAX_NAMED] if (ln := _line(sym, rel, q))]
    if len(quals) > MAX_NAMED:
        out.append(f"(+{len(quals) - MAX_NAMED} more definitions)")
    return [*out, _BOUND] if out else []
