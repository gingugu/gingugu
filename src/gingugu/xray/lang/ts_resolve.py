"""Resolve a TS/JS specifier to a repo file: relative, tsconfig aliases, workspace packages."""

from __future__ import annotations

import posixpath
from collections.abc import Container

from .ts_config import Config, Manifests, leaves
from .ts_paths import join, resolve_file

# (target path, certain)
Hit = tuple[str, bool] | None


def _match_paths(paths: dict[str, list[str]], spec: str) -> list[str]:
    """Targets of the best-matching ``paths`` key (longest prefix, like tsc), substituted.

    Only the winner is substituted: building every candidate's targets eagerly
    let one long specifier against a large map allocate without bound."""
    best: tuple[int, list[str], str] | None = None
    for key, targets in paths.items():
        if "*" not in key:
            if key == spec:
                return targets
            continue
        prefix, _, suffix = key.partition("*")
        if (
            len(spec) >= len(prefix) + len(suffix)
            and spec.startswith(prefix)
            and spec.endswith(suffix)
            and (best is None or len(prefix) > best[0])
        ):
            best = (len(prefix), targets, spec[len(prefix) : len(spec) - len(suffix)])
    if best is None:
        return []
    return [t.replace("*", best[2], 1) for t in best[1]]


def _prefixes(spec: str):
    """``spec`` and each of its ``/``-boundary prefixes, longest first."""
    while spec:
        yield spec
        spec = spec.rpartition("/")[0]


class Resolver:
    def __init__(self, files: Container[str], manifests: Manifests) -> None:
        self.files = files
        self.manifests = manifests
        self.workspace = manifests.workspace()
        # A bare specifier repeats across files under one config: resolve it once.
        self._bare: dict[tuple[int, str], Hit] = {}

    def relative(self, src: str, spec: str) -> str | None:
        base = join(posixpath.dirname(src), spec)
        return resolve_file(base, self.files) if base is not None else None

    def bare(self, src: str, spec: str) -> Hit:
        """Aliases (paths, baseUrl) then in-repo workspace packages; None means external."""
        cfg = self.manifests.governing(src)
        key = (id(cfg), spec)
        if key not in self._bare:
            hit = self._aliased(cfg, spec) if cfg is not None else None
            self._bare[key] = (hit, True) if hit else self._workspace(spec)
        return self._bare[key]

    def _aliased(self, cfg: Config, spec: str) -> str | None:
        if cfg.paths:
            root = cfg.base_url if cfg.base_url is not None else cfg.paths_dir
            for target in _match_paths(cfg.paths, spec):
                base = join(root, target)
                hit = resolve_file(base, self.files) if base is not None else None
                if hit:
                    return hit
        if cfg.base_url is not None:
            base = join(cfg.base_url, spec)
            return resolve_file(base, self.files) if base is not None else None
        return None

    def _workspace(self, spec: str) -> Hit:
        owner = next((p for p in _prefixes(spec) if p in self.workspace), None)
        if owner is None:
            return None
        directory, data = self.workspace[owner]
        sub = spec[len(owner) + 1 :]
        if sub:
            return self._subpath(directory, data, sub)
        return self._entry(directory, data)

    def _in_package(self, directory: str, leaf: str) -> str | None:
        base = join(directory, leaf)
        return resolve_file(base, self.files) if base is not None and "*" not in leaf else None

    def _subpath(self, directory: str, data: dict, sub: str) -> Hit:
        exports = data.get("exports")
        if isinstance(exports, dict) and f"./{sub}" in exports:
            for leaf in leaves(exports[f"./{sub}"]):
                hit = self._in_package(directory, leaf)
                if hit:
                    return hit, True
        hit = self._in_package(directory, sub)
        return (hit, True) if hit else None

    def _entry(self, directory: str, data: dict) -> Hit:
        exports = data.get("exports")
        if isinstance(exports, dict) and "." in exports:
            exports = exports["."]
        elif isinstance(exports, dict) and any(k.startswith("./") for k in exports):
            exports = None  # subpath-only map: no root entry
        for field in (exports, data.get("module"), data.get("main")):
            for leaf in leaves(field):
                hit = self._in_package(directory, leaf)
                if hit:
                    return hit, True
        hit = resolve_file(directory, self.files)
        if hit:
            return hit, True
        # The entry points at build output (dist/); the source index is a good guess.
        hit = resolve_file(posixpath.join(directory, "src/index"), self.files)
        return (hit, False) if hit else None
