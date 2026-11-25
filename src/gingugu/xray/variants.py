"""Pure core of ``code_xray`` action ``variants``: names and drift, no I/O.

``file_drift`` scores one shared file; ``summary`` folds those scores into a
flavour's drift. The tool scores each file as it reads it, so no more than one
pair of files is ever held at once, and ``drift`` is the same fold over two
whole trees for a caller that already holds them.
"""

from __future__ import annotations

import difflib

# The differing middle of one file, lines x lines, that difflib may diff: ~2000 x 2000.
# difflib is quadratic or worse on repeated lines (every "}"); past this the whole
# middle counts as changed - an upper bound, never a stall.
MAX_DIFF_CELLS = 4_000_000


def stem(name: str) -> str:
    """The name without its last ``-segment``; unchanged when that would leave nothing."""
    low = name.lower()
    head, sep, tail = low.rpartition("-")
    return head if sep and tail else low


def flavours(me: str, names: list[str]) -> list[str]:
    """The names that are flavours of ``me``: same stem, or one is the other's stem."""
    mine, low = stem(me), me.lower()
    return sorted(n for n in names if n != me and (mine in (stem(n), n.lower()) or stem(n) == low))


def file_drift(a: bytes, b: bytes, *, exact: bool = True) -> tuple[int, int]:
    """(changed lines, lines) for one shared file. ``exact=False`` (out of time)
    skips difflib: the differing middle counts as changed."""
    if b"\0" in a or b"\0" in b:
        return (0 if a == b else 1), 1
    if a == b:
        return 0, len(a.splitlines())
    x, y = a.splitlines(), b.splitlines()
    lines = max(len(x), len(y))
    head = 0  # an edit rarely touches the whole file: match the common ends for free
    while head < min(len(x), len(y)) and x[head] == y[head]:
        head += 1
    tail = 0
    while tail < min(len(x), len(y)) - head and x[-1 - tail] == y[-1 - tail]:
        tail += 1
    mx, my = x[head : len(x) - tail], y[head : len(y) - tail]
    if not exact or len(mx) * len(my) > MAX_DIFF_CELLS:
        return max(len(mx), len(my)), lines
    blocks = difflib.SequenceMatcher(None, mx, my, autojunk=False).get_matching_blocks()
    return max(len(mx), len(my)) - sum(m.size for m in blocks), lines


def summary(
    scored: dict[str, tuple[int, int, bool]], only_here: set[str], only_there: set[str]
) -> dict:
    """A flavour's drift from ``scored``: path -> (changed, lines, identical)."""
    changed = sum(c for c, _, _ in scored.values())
    total = sum(n for _, n, _ in scored.values())
    rows = [{"path": p, "changed": c, "lines": n} for p, (c, n, same) in scored.items() if not same]
    rows.sort(key=lambda r: (-r["changed"], r["path"]))
    return {
        "shared": len(scored),
        "identical": len(scored) - len(rows),
        "differing": len(rows),
        "only_here": sorted(only_here),
        "only_there": sorted(only_there),
        "changed_lines": changed,
        "total_lines": total,
        "drift": changed / total if total else 0.0,
        "files": rows,
    }


def drift(mine: dict[str, bytes], theirs: dict[str, bytes]) -> dict:
    """Changed lines over lines across the files both trees hold."""
    scored = {
        p: (*file_drift(mine[p], theirs[p]), mine[p] == theirs[p])
        for p in sorted(mine.keys() & theirs.keys())
    }
    return summary(scored, mine.keys() - theirs.keys(), theirs.keys() - mine.keys())
