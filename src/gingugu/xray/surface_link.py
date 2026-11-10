"""X7d: what a symbol linker keeps for the attack-surface map while it links.

Every language's linker already decides, call by call, whether a call resolves to
a definition, leaves the repo (external), or cannot be proven. It hands each
verdict here:

- an external call whose dotted name the catalogue lists becomes a *sink*
  (``surface_catalog.sink``); any other external target is never stored;
- a ``sql`` walk record becomes the sink ``sql.built`` where it sits;
- an ``entries`` walk record becomes an :class:`Entry`: a decorated definition
  is its own start node; a handler passed by name is resolved by the linker
  exactly as a call of that form from that scope would be; an inline callback
  starts from what the calls inside its line span resolved to, and keeps the
  sinks it calls itself.

Per file: ``file`` before the file's calls, ``call`` for each, ``close`` after.
"""

from __future__ import annotations

from collections.abc import Callable

from . import surface_catalog as cat
from .symbols_model import Entry, Node, Sink
from .validate import is_valid_path

Resolve = Callable[[list], Node | None]  # a call-shaped record -> (path, qual) or None


def placeable(e: Entry) -> bool:
    """Whether every path an entry names passes the brain's own path rule."""
    return is_valid_path(e.path) and all(is_valid_path(p) for p, _ in e.nodes)


class Collector:
    def __init__(self, lang: str, defs: dict[str, dict]) -> None:
        self.lang, self.defs = lang, defs
        self.sinks: list[Sink] = []
        self.entries: list[Entry] = []
        self._recs: list[list] = []
        self._spans: dict[str, list[tuple[int, int, set, list]]] = {}  # scope -> inline spans

    def file(self, rel: str, raw: dict) -> None:
        self._recs = [r for r in raw.get("entries", []) if cat.entry_kind(self.lang, r[1])]
        self._spans = {}
        for r in self._recs:
            if r[2] == "i":
                self._spans.setdefault(r[0], []).append((r[6], r[7], set(), []))
        for scope, line in raw.get("sql", []):
            self._sink(rel, scope, cat.SQL_BUILT, line)

    def _inline(self, scope: str, line: int):
        return [s for s in self._spans.get(scope, ()) if s[0] <= line <= s[1]]

    def _sink(self, rel: str, scope: str, name: str, line: int) -> None:
        self.sinks.append((rel, scope, name, line))
        for span in self._inline(scope, line):
            span[3].append((name, line))

    def call(self, rel: str, rec: list, node: Node | None, ext: str | None) -> None:
        """One linked call record: ``node`` when it resolved in the repo, ``ext``
        the dotted name of the external (or global) target it named, else None."""
        if node is not None:
            for span in self._inline(rec[0], rec[4]):
                span[2].add(node)
        elif ext is not None and cat.sink(self.lang, ext) is not None:
            self._sink(rel, rec[0], ext, rec[4])

    def close(self, rel: str, resolve: Resolve) -> None:
        """The file's entries, now its calls are in. ``resolve`` answers a handler
        passed by name as the linker answers a call record of that shape."""
        spans = {scope: list(found) for scope, found in self._spans.items()}
        for scope, label, form, x, y, line, start, end in self._recs:
            kind = cat.entry_kind(self.lang, label)
            if form == "i":
                span = next(s for s in spans[scope] if (s[0], s[1]) == (start, end))
                nodes, direct = tuple(sorted(span[2])), tuple(sorted(set(span[3])))
                self.entries.append(Entry(rel, label, kind, line, scope, nodes, direct, True))
                continue
            if form == "d":
                node = (rel, x) if x in self.defs.get(rel, {}) else None
            else:
                node = resolve([scope, form, x, y, line])
            handler = node[1] if node else (x if form in ("d", "n") else f"{x}.{y}")
            self.entries.append(Entry(rel, label, kind, line, handler, (node,) if node else ()))
        self._recs, self._spans = [], {}
