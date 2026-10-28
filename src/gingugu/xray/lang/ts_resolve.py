"""Resolve a TS/JS specifier to a repo file: relative, tsconfig aliases, workspace packages."""

from __future__ import annotations

import posixpath

from .ts_config import Config, Manifests, leaves
from .ts_paths import join, resolve_file

# (target path, certain)
Hit = tuple[str, bool] | None


def _match_paths(paths: dict[str, list[str]], spec: str) -> list[str]:
    """Targets of the best-matching ``paths`` key (longest prefix, like tsc), substituted."""
    best: tuple[int, list[str]] | None = None
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
            cap = spec[len(prefix) : len(spec) - len(suffix)]
            best = (len(prefix), [t.replace("*", cap, 1) for t in targets])
    return best[1] if best else []


class Resolver:
    def __init__(self, files: dict[str, bytes], manifests: Manifests) -> None:
        self.files = files
        self.manifests = manifests
        self.workspace = manifests.workspace()

    def relative(self, src: str, spec: str) -> str | None:
        base = join(posixpath.dirname(src), spec)
        return resolve_file(base, self.files) if base is not None else None

    def bare(self, src: str, spec: str) -> Hit:
        """Aliases (paths, baseUrl) then in-repo workspace packages; None means external."""
        cfg = self.manifests.governing(src)
        if cfg is not None:
            hit = self._aliased(cfg, spec)
            if hit:
                return hit, True
        return self._workspace(spec)

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
        owner = None
        for name in self.workspace:
            if (spec == name or spec.startswith(name + "/")) and (
                owner is None or len(name) > len(owner)
            ):
                owner = name
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
