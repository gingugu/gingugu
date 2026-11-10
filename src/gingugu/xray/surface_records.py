"""X7d: the walk records every language's symbol walk adds, and their shape check.

Each language's symbol walk returns, beside its X17 records:

- ``entries``: ``[scope, label, form, x, y, line, start, end, method, route, recv]``
  - an entry point. ``method`` and ``route`` are what it answers to, in the shape
  ``surface_route`` defines (``""`` when the source gives no literal); the route
  carries any prefix the same file gives its router. ``recv`` is the router it is
  registered on, for prefixes set in other files (``surface_mounts``): the root
  name of the receiver (``router`` for ``router.get``; a group's root router for
  ``v1.GET`` when ``v1 := r.Group(...)``), ``#<i>`` when that root is the i-th
  parameter of ``scope``, ``""`` when there is no receiver (a bare decorator, a
  registrar like ``path``, ``main``).
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

- ``mounts``: ``[scope, line, parent, prefix, form, x, y, arg]`` - a router mounted
  under a prefix: routes on the child live under ``prefix`` on ``parent``.
  ``parent`` is a receiver root like ``recv`` (``""``: none to name). ``prefix``
  is a route-shaped string (``surface_route``), with any prefix the same file gives
  ``parent`` already joined in front. ``form`` names the child:

  - ``"n"`` a name ``x``; ``"a"`` ``x.y`` (a module's attribute);
  - ``"m"`` a dotted module ``x`` (Django ``include("app.urls")``);
  - ``"r"`` a module specifier ``x`` (TS ``require("./routes")``);
  - ``"cn"``/``"ca"`` a call of ``x`` / ``x.y``: every router that function builds;
  - ``"pn"``/``"pa"`` a router passed as argument ``arg`` to ``x`` / ``x.y``:
    the function's ``#<arg>`` parameter;
  - ``"g"`` a global prefix (NestJS ``setGlobalPrefix``); ``x``/``y`` are "".

  ``arg`` is 0 for every form but ``"p*"``.

All are rendered into the agent's context from an untrusted cache, so a record
of any other shape sends the file to be parsed again.
"""

from __future__ import annotations

import re

from . import surface_catalog as cat
from . import surface_route as sr
from .symbols_walk import is_qual

FORMS = ("d", "n", "a", "s", "i")
MOUNT_FORMS = ("n", "a", "m", "r", "cn", "ca", "pn", "pa", "g")
KEYS = ("entries", "sql", "mounts")
MAX_ARG = 64
_PARAM = re.compile(r"#\d{1,2}\Z")


MAX_LINE = 10_000_000  # a line number past any real file is a crafted one
MAX_NAME = 512


def _int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_LINE


def _qual(v: object, module_ok: bool = False) -> bool:
    return isinstance(v, str) and len(v) <= MAX_NAME and is_qual(v, module_ok=module_ok)


def _ident(v: object) -> bool:
    return _qual(v) and "." not in v


def is_recv(v: object) -> bool:
    """A receiver root: "", an identifier, or ``#<i>`` (a parameter)."""
    return v == "" or _ident(v) or (isinstance(v, str) and bool(_PARAM.match(v)))


def is_entry(rec: object, lang: str) -> bool:
    if not (isinstance(rec, list) and len(rec) == 11 and all(_int(v) for v in rec[5:8])):
        return False
    scope, label, form, x, y, _line, start, end, method, route, recv = rec
    if not (sr.is_method(method) and sr.is_route(route) and is_recv(recv)):
        return False
    if not (_qual(scope, module_ok=True) and isinstance(form, str) and form in FORMS):
        return False
    if not (isinstance(label, str) and cat.entry_kind(lang, label)):
        return False
    if form == "i":
        return x == "" and y == "" and 0 < start <= end
    if start or end:
        return False
    if form == "d":
        return _qual(x) and y == ""
    if form == "n":
        return _ident(x) and y == ""
    return (_qual(x) if form == "s" else _ident(x)) and _ident(y)


def is_mount(rec: object) -> bool:
    if not (isinstance(rec, list) and len(rec) == 8):
        return False
    scope, line, parent, prefix, form, x, y, arg = rec
    if not (_qual(scope, module_ok=True) and _int(line) and is_recv(parent)):
        return False
    if not (sr.is_route(prefix) and isinstance(form, str) and form in MOUNT_FORMS):
        return False
    if not (isinstance(arg, int) and not isinstance(arg, bool) and 0 <= arg <= MAX_ARG):
        return False
    if arg and form[0] != "p":
        return False
    if form == "g":
        return x == "" and y == ""
    if form == "m":
        return _qual(x) and y == ""
    if form == "r":
        return isinstance(x, str) and sr.clean(x) == x and y == ""
    if form in ("n", "cn", "pn"):
        return _ident(x) and y == ""
    return _ident(x) and _ident(y)  # "a", "ca", "pa"


def is_sql(rec: object) -> bool:
    return isinstance(rec, list) and len(rec) == 2 and _qual(rec[0], True) and _int(rec[1])


def valid(raw: dict, lang: str) -> bool:
    """Whether ``raw``'s ``entries``, ``sql`` and ``mounts`` are exactly the walk's shape."""
    entries, sql, mounts = raw.get("entries"), raw.get("sql"), raw.get("mounts")
    return (
        isinstance(entries, list)
        and isinstance(sql, list)
        and isinstance(mounts, list)
        and all(is_entry(r, lang) for r in entries)
        and all(is_sql(r) for r in sql)
        and all(is_mount(r) for r in mounts)
    )
