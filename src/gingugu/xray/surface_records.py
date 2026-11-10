"""X7d: the two walk records every language's symbol walk adds, and their shape check.

Each language's symbol walk returns, beside its X17 records:

- ``entries``: ``[scope, label, form, x, y, line, start, end]`` - an entry point.
  ``label`` is the decorator or registrar as written (``mcp.tool``, ``app.get``,
  ``http.HandleFunc``) and must be one ``surface_catalog.entry_kind`` knows for
  the language. ``scope`` is the qual the record sits in (``<module>`` at top
  level). ``form`` says what the handler is:

  - ``"d"``: a decorated definition (or Go's ``main``); ``x`` is its qual, ``y`` "".
  - ``"n"``/``"a"``/``"s"``: a handler passed by name - ``x``, ``x.y``,
    ``self.y``/``this.y`` (``x`` then the class qual) - resolved at link time
    exactly like a call record of that form made from ``scope``.
  - ``"i"``: an inline callback (an arrow, a function literal); ``x``/``y`` are
    "" and ``start``-``end`` is its line span. Its calls already belong to
    ``scope``; the span picks out the ones the callback makes.

  ``line`` is the decorator's or the registering call's line; ``start``/``end``
  are 0 for every form but ``"i"``.

- ``sql``: ``[scope, line]`` - a query-running call (``surface_catalog.SQL_METHODS``)
  handed a string built at runtime from a part that is not known safe.

Both are rendered into the agent's context from an untrusted cache, so a record
of any other shape sends the file to be parsed again.
"""

from __future__ import annotations

from . import surface_catalog as cat
from .symbols_walk import is_qual

FORMS = ("d", "n", "a", "s", "i")
KEYS = ("entries", "sql")


def _int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _ident(v: object) -> bool:
    return is_qual(v) and "." not in v


def is_entry(rec: object, lang: str) -> bool:
    if not (isinstance(rec, list) and len(rec) == 8 and all(_int(v) for v in rec[5:])):
        return False
    scope, label, form, x, y, _line, start, end = rec
    if not (is_qual(scope, module_ok=True) and isinstance(form, str) and form in FORMS):
        return False
    if not (isinstance(label, str) and cat.entry_kind(lang, label)):
        return False
    if form == "i":
        return x == "" and y == "" and 0 < start <= end
    if start or end:
        return False
    if form == "d":
        return is_qual(x) and y == ""
    if form == "n":
        return _ident(x) and y == ""
    return (is_qual(x) if form == "s" else _ident(x)) and _ident(y)


def is_sql(rec: object) -> bool:
    return isinstance(rec, list) and len(rec) == 2 and is_qual(rec[0], True) and _int(rec[1])


def valid(raw: dict, lang: str) -> bool:
    """Whether ``raw["entries"]`` and ``raw["sql"]`` are exactly the walk's shape."""
    entries, sql = raw.get("entries"), raw.get("sql")
    return (
        isinstance(entries, list)
        and isinstance(sql, list)
        and all(is_entry(r, lang) for r in entries)
        and all(is_sql(r) for r in sql)
    )
