"""X7c: what one file says about database tables - names and lines only.

Pure text in, ``(defines, drops, uses)`` out, each ``(name, 1-based line)`` in line
order, names lowercased, one entry per name per line. Only literal names in known
forms count: DDL (``CREATE TABLE``, ``DROP TABLE``, ``ALTER TABLE ... RENAME``),
ORM declarations (``__tablename__``, ``Table(...)``, alembic ``op.create_table``)
and UPPERCASE SQL keywords (``FROM``, ``JOIN``, ``INTO``, ``UPDATE``). Lowercase
SQL is not read: prose and Python imports look the same. No value is kept.
"""

from __future__ import annotations

import re
from typing import NamedTuple

Pairs = list[tuple[str, int]]

# An identifier: optional quote, optional ``schema.`` prefix, the name, optional quote.
# The name must end where an identifier ends: one running into ``{``/``$`` is a
# template built at run time, into ``/`` or ``:`` an image or a host, into one
# ``-`` a word like ``base-py``. A ``--`` comment still ends it. A name before a
# ``.`` is a schema, never a table: ``ghcr.io/x`` must not backtrack into ``ghcr``.
_END = r"(?![\w{$/:.]|-(?!-))"
_ID = rf"""["`\[]?(?:([A-Za-z0-9_]+)["`\]]?\.["`\[]?)?([A-Za-z_][A-Za-z0-9_]*){_END}["`\]]?"""
_LIT = r"""["']([A-Za-z_][A-Za-z0-9_]*)["']"""
_IF_NOT = r"(?:IF\s+NOT\s+EXISTS\s+)?"
_IF = r"(?:IF\s+EXISTS\s+)?"


class _Keywords(NamedTuple):
    create: re.Pattern
    rename: re.Pattern
    drop: re.Pattern
    alter: re.Pattern
    use: re.Pattern
    on: re.Pattern
    on_line: re.Pattern


def _keywords(flags: int) -> _Keywords:
    return _Keywords(
        re.compile(
            rf"\bCREATE\s+(?:TEMP(?:ORARY)?\s+)?(?:VIRTUAL\s+)?(?:TABLE|VIEW)\s+{_IF_NOT}{_ID}",
            flags,
        ),
        re.compile(rf"\bALTER\s+TABLE\s+{_IF}{_ID}\s+RENAME\s+TO\s+{_ID}", flags),
        re.compile(rf"\bDROP\s+TABLE\s+{_IF}{_ID}", flags),
        re.compile(rf"\bALTER\s+TABLE\s+{_IF}{_ID}", flags),
        re.compile(rf"\b(?:FROM|JOIN|INTO|UPDATE)\s+{_ID}", flags),
        re.compile(rf"\bON\s+{_ID}", flags),
        re.compile(r"\bCREATE\s+(?:UNIQUE\s+INDEX|INDEX|TRIGGER)\b", flags),
    )


# Code needs UPPERCASE keywords: Python imports and prose look like lowercase SQL.
# A .sql file holds neither, so there any case is SQL.
_UPPER = _keywords(0)
_ANY_CASE = _keywords(re.IGNORECASE)
_LITERAL_DEFINES = (
    re.compile(rf"\b__tablename__\s*=\s*{_LIT}"),
    re.compile(rf"\bTable\(\s*{_LIT}\s*,"),
    re.compile(rf"\bop\.create_table\(\s*{_LIT}"),
)
_LITERAL_DROP = re.compile(rf"\bop\.drop_table\(\s*{_LIT}")

_VALID = re.compile(r"[a-z_][a-z0-9_]{1,63}")  # two letters at least: one is an alias
_SQL_WORDS = frozenset(
    "select set values where conflict delete the and or not exists if table only each row on "
    "a an as by in is it its of to with from into join update insert alter drop create index "
    "view trigger temp temporary virtual using default null true false this that any all one "
    "existing begin end for after before instead when then else case lateral unnest dual "
    "returning".split()
)
_BUILTIN_PREFIXES = ("sqlite_", "pragma_", "pg_")
_BUILTIN_SCHEMAS = frozenset({"information_schema", "pg_catalog"})
_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def kind(rel: str) -> str | None:
    """Which language ``rel`` is, by name alone; None for anything else."""
    base = rel.rsplit("/", 1)[-1]
    ext = base[base.rfind(".") :].lower() if "." in base else ""
    if ext == ".py":
        return "py"
    if ext == ".sql":
        return "sql"
    if ext in _JS_EXTS:
        return "js"
    if ext == ".go":
        return "go"
    return None


def valid_name(value: object) -> bool:
    return isinstance(value, str) and _VALID.fullmatch(value) is not None


def _table(schema: str | None, name: str) -> str | None:
    """The stored name, or None for an alias, a SQL word or an engine builtin."""
    low = name.lower()
    if schema is not None and schema.lower() in _BUILTIN_SCHEMAS:
        return None
    if low in _SQL_WORDS or low.startswith(_BUILTIN_PREFIXES) or not valid_name(low):
        return None
    return low


def _line(
    line: str, kw: _Keywords
) -> tuple[list[tuple[int, str]], list[tuple[int, str]], list[tuple[int, str]]]:
    """(defines, drops, uses) of one line as ``(position, name)``."""
    defines: list[tuple[int, str]] = []
    drops: list[tuple[int, str]] = []
    uses: list[tuple[int, str]] = []

    def add(into: list, pos: int, schema: str | None, name: str) -> None:
        found = _table(schema, name)
        if found is not None:
            into.append((pos, found))

    for m in kw.create.finditer(line):
        add(defines, m.start(), m.group(1), m.group(2))
    renamed: set[int] = set()
    for m in kw.rename.finditer(line):
        renamed.add(m.start())
        add(drops, m.start(), m.group(1), m.group(2))
        add(defines, m.start(), m.group(3), m.group(4))
    for m in kw.drop.finditer(line):
        add(drops, m.start(), m.group(1), m.group(2))
    for pat in _LITERAL_DEFINES:
        for m in pat.finditer(line):
            add(defines, m.start(), None, m.group(1))
    for m in _LITERAL_DROP.finditer(line):
        add(drops, m.start(), None, m.group(1))
    for m in kw.alter.finditer(line):
        if m.start() not in renamed:
            add(uses, m.start(), m.group(1), m.group(2))
    for m in kw.use.finditer(line):
        add(uses, m.start(), m.group(1), m.group(2))
    if kw.on_line.search(line):
        for m in kw.on.finditer(line):
            add(uses, m.start(), m.group(1), m.group(2))
    return defines, drops, uses


def _add(out: Pairs, found: list[tuple[int, str]], number: int) -> None:
    seen: set[str] = set()  # this line only: a whole-list check is quadratic per file
    for _, name in sorted(found):
        if name not in seen:
            seen.add(name)
            out.append((name, number))


def scan_text(rel: str, text: str) -> tuple[Pairs, Pairs, Pairs]:
    """(defines, drops, uses) of ``text`` as the file ``rel``."""
    defines: Pairs = []
    drops: Pairs = []
    uses: Pairs = []
    which = kind(rel)
    if which is None:
        return defines, drops, uses
    kw = _ANY_CASE if which == "sql" else _UPPER
    for number, line in enumerate(text.splitlines(), 1):
        d, x, u = _line(line, kw)
        _add(defines, d, number)
        _add(drops, x, number)
        _add(uses, u, number)
    return defines, drops, uses
