"""Terraform reader for the env map: names set inside ``environment`` blocks.

One pass over the lines, one pass over the characters of each line inside a block;
brackets and ``#``/``//`` comments only count outside double-quoted strings. Names
and lines only - a value is skipped, never kept.
"""

from __future__ import annotations

import re

# No two whitespace runs may touch: `\s*=?\s*` is quadratic on a line of spaces.
_OPEN = re.compile(r'^[ \t]*(?:"environment"[ \t]*:[ \t]*|environment[ \t]*(?:=[ \t]*)?)[\[{]')
_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _string_end(line: str, i: int) -> int:
    """Index just past the double-quoted string opening at ``line[i]``."""
    n = len(line)
    i += 1
    while i < n:
        if line[i] == "\\":
            i += 2
        elif line[i] == '"':
            return i + 1
        else:
            i += 1
    return n


def _skip_spaces(line: str, i: int) -> int:
    while i < len(line) and line[i] in " \t":
        i += 1
    return i


def _is_assign(line: str, i: int, signs: str) -> int:
    """Index just past an assignment sign at/after ``i`` (spaces allowed), else -1."""
    i = _skip_spaces(line, i)
    if i < len(line) and line[i] in signs:
        if line[i] == "=" and line[i + 1 : i + 2] == "=":
            return -1
        return i + 1
    return -1


def _name_value(line: str, i: int, valid) -> tuple[int, str | None]:
    """After a ``name`` key: the quoted NAME that follows its ``=``/``:``, if any."""
    after = _is_assign(line, i, "=:")
    if after < 0:
        return i, None
    j = _skip_spaces(line, after)
    if j < len(line) and line[j] == '"':
        end = _string_end(line, j)
        value = line[j + 1 : end - 1]
        if valid(value):
            return end, value
    return after, None


def _scan(line: str, start: int, depth: int, valid) -> tuple[int, list[tuple[int, str]]]:
    """Walk ``line`` from ``start``; return (depth after, [(column, NAME)])."""
    found: list[tuple[int, str]] = []
    i, n = start, len(line)
    while i < n:
        c = line[i]
        if c == "#" or (c == "/" and line[i + 1 : i + 2] == "/"):
            break
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth <= 0:
                return 0, found
        elif c == '"':
            end = _string_end(line, i)
            body = line[i + 1 : end - 1]
            if body == "name":
                i, value = _name_value(line, end, valid)
                if value is not None:
                    found.append((i, value))
                continue
            if valid(body) and _is_assign(line, end, "=") >= 0 and line[i - 1 : i] != ".":
                found.append((i, body))
            i = end
            continue
        elif c in _WORD:
            j = i
            while j < n and line[j] in _WORD:
                j += 1
            word, prev = line[i:j], line[i - 1 : i]
            if prev != "." and word == "name":
                i, value = _name_value(line, j, valid)
                if value is not None:
                    found.append((i, value))
                continue
            if prev != "." and valid(word) and _is_assign(line, j, "=") >= 0:
                found.append((i, word))
            i = j
            continue
        i += 1
    return depth, found


def sets(text: str) -> list[tuple[str, int]]:
    """(NAME, 1-based line) of every name an ``environment`` block sets, in order."""
    from gingugu.xray.env_scan import _NAME, _add  # env_scan imports this module

    def valid(word: str) -> bool:
        return _NAME.fullmatch(word) is not None

    out: list[tuple[str, int]] = []
    depth = 0
    for number, line in enumerate(text.splitlines(), 1):
        head = line.lstrip()
        if head.startswith("#") or head.startswith("//"):
            continue
        if depth == 0:
            opened = _OPEN.match(line)
            if not opened:
                continue
            start = opened.end() - 1
        else:
            start = 0
        depth, found = _scan(line, start, depth, valid)
        _add(out, found, number)
    return out
