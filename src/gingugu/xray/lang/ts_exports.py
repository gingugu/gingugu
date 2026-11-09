"""Which repo file a TS/JS specifier names, and which definition an export names.

The symbol linker's view of modules: specifiers go through the same ``Resolver``
as the import graph, and a name is followed through ``export {x} from`` and
``export *`` barrels to the file that defines it.
"""

from __future__ import annotations

from .ts_paths import ASSET_EXTS, MAX_SPEC, asset_ext, is_relative, strip_query
from .ts_resolve import Resolver

EXT = "external"
MAX_HOPS = 8  # barrels deeper than this are not followed: the call stays unresolved


class Modules:
    def __init__(self, raws: dict[str, dict], resolver: Resolver) -> None:
        self.resolver = resolver
        self.defs = {rel: {d[0] for d in raw["defs"]} for rel, raw in raws.items()}
        self.exports = {rel: raw["exports"] for rel, raw in raws.items()}
        self._target: dict[tuple[str, str], str | None] = {}

    def target(self, rel: str, spec: str) -> str | None:
        """The repo file ``spec`` names from ``rel``; ``EXT`` for a package, None when
        a relative path leads nowhere."""
        key = (rel, spec)
        if key not in self._target:
            self._target[key] = self._resolve(rel, spec)
        return self._target[key]

    def _resolve(self, rel: str, spec: str) -> str | None:
        spec = strip_query(spec)
        if not spec or len(spec) > MAX_SPEC:
            return EXT
        if is_relative(spec):
            return self.resolver.relative(rel, spec)
        if asset_ext(spec) in ASSET_EXTS:
            return None
        hit = self.resolver.bare(rel, spec)
        return hit[0] if hit else EXT

    def export_of(self, path: str, name: str) -> tuple[str, str] | None:
        """``(file, qual)`` of the top-level definition ``name`` that ``path`` exports,
        following re-exports. Each (file, name) is visited once, so cycles end."""
        seen: set[tuple[str, str]] = set()
        stack = [(path, name, 0)]
        while stack:
            file, want, hops = stack.pop()
            if (file, want) in seen or hops > MAX_HOPS:
                continue
            seen.add((file, want))
            defs = self.defs.get(file, ())
            if want in defs and want != "default":
                return file, want
            named, stars = [], []
            for exported, spec, orig in self.exports.get(file, ()):
                if not spec:
                    if exported == want and orig in defs:
                        return file, orig
                elif exported == want:
                    named.append((spec, orig))
                elif exported == "*" and want != "default":
                    stars.append((spec, want))
            # the stack pops last-in first: named re-exports are tried before stars
            for spec, orig in [*reversed(stars), *reversed(named)]:
                nxt = self.target(file, spec)
                if nxt is not None and nxt != EXT:
                    stack.append((nxt, orig, hops + 1))
        return None
