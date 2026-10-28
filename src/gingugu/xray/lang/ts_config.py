"""tsconfig/jsconfig and package.json, read as data: path aliases, workspaces, entries."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass

from . import jsonc
from .ts_paths import join, norm, resolve_file

CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
_SCRIPT_SRC = re.compile(r"<script\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I | re.S)


@dataclass
class Config:
    """The aliasing a tsconfig (with its ``extends`` chain) gives its files."""

    base_url: str | None = None  # repo path; set by the config that wrote baseUrl
    paths: dict[str, list[str]] | None = None
    paths_dir: str = ""  # what ``paths`` targets are relative to, absent a baseUrl

    def over(self, top: Config) -> Config:
        return Config(
            top.base_url if top.base_url is not None else self.base_url,
            top.paths if top.paths is not None else self.paths,
            top.paths_dir if top.paths is not None else self.paths_dir,
        )


def _paths_map(raw: object) -> dict[str, list[str]] | None:
    if not isinstance(raw, dict):
        return None
    return {k: [t for t in v if isinstance(t, str)] for k, v in raw.items() if isinstance(v, list)}


def _parse(data: bytes, jsonc_file: bool) -> object:
    try:
        return jsonc.loads(data) if jsonc_file else json.loads(data.decode("utf-8-sig"))
    except ValueError:  # JSONDecodeError and UnicodeDecodeError both subclass it
        return None


def leaves(value: object) -> list[str]:
    """Every string in a nested str/list/dict structure, in document order."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in leaves(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in leaves(v)]
    return []


class Manifests:
    """Every config/package manifest, parsed once; malformed ones go to ``skipped``."""

    def __init__(self, manifests: dict[str, bytes], skipped: list[tuple[str, str]]) -> None:
        self.configs: dict[str, dict] = {}
        self.packages: dict[str, dict] = {}
        self.html: dict[str, str] = {}
        for path in sorted(manifests):
            name = posixpath.basename(path)
            if name == "index.html":
                self.html[path] = manifests[path].decode("utf-8", "replace")
                continue
            is_pkg = name == "package.json"
            obj = _parse(manifests[path], jsonc_file=not is_pkg)
            if isinstance(obj, dict):
                (self.packages if is_pkg else self.configs)[path] = obj
            else:
                skipped.append((path, "malformed"))
        self._effective: dict[str, Config] = {}

    # --- tsconfig -----------------------------------------------------------

    def _extends(self, path: str, value: object) -> list[str]:
        found = []
        for ref in value if isinstance(value, list) else [value]:
            if not isinstance(ref, str) or not ref.startswith("."):
                continue  # a package config (@tsconfig/...) is not in the repo
            target = join(posixpath.dirname(path), ref)
            if target is None:
                continue
            for cand in (target, f"{target}.json"):
                if cand in self.configs:
                    found.append(cand)
                    break
        return found

    def effective(self, path: str, seen: tuple[str, ...] = ()) -> Config:
        if path in self._effective:
            return self._effective[path]
        here = posixpath.dirname(path)
        cfg = Config(paths_dir=here)
        if path in seen:
            return cfg
        data = self.configs[path]
        for parent in self._extends(path, data.get("extends")):
            cfg = cfg.over(self.effective(parent, (*seen, path)))
        opts = data.get("compilerOptions")
        opts = opts if isinstance(opts, dict) else {}
        base = opts.get("baseUrl")
        cfg = cfg.over(
            Config(
                join(here, base) if isinstance(base, str) else None,
                _paths_map(opts.get("paths")),
                here,
            )
        )
        if not seen:  # a result computed inside a cycle was cut short; don't cache it
            self._effective[path] = cfg
        return cfg

    def governing(self, src: str) -> Config | None:
        """The nearest tsconfig.json/jsconfig.json up from ``src``."""
        d = posixpath.dirname(src)
        while True:
            for name in CONFIG_NAMES:
                cand = posixpath.join(d, name) if d else name
                if cand in self.configs:
                    return self.effective(cand)
            if not d:
                return None
            d = posixpath.dirname(d)

    # --- workspace packages -------------------------------------------------

    def workspace(self) -> dict[str, tuple[str, dict]]:
        """Package name -> (directory, package.json), for each package that names itself."""
        names: dict[str, tuple[str, dict]] = {}
        for path, data in self.packages.items():
            name = data.get("name")
            if isinstance(name, str) and name:
                names.setdefault(name, (posixpath.dirname(path), data))
        return names

    # --- entries ------------------------------------------------------------

    def entries(self, files: dict[str, bytes]) -> set[str]:
        found: set[str] = set()
        for path, data in self.packages.items():
            here = posixpath.dirname(path)
            fields = [data.get(k) for k in ("main", "module", "bin", "exports")]
            for leaf in leaves(fields):
                base = None if "*" in leaf else join(here, leaf)
                hit = resolve_file(base, files) if base is not None else None
                if hit:
                    found.add(hit)
        for path, html in self.html.items():
            here = posixpath.dirname(path)
            for ref in _SCRIPT_SRC.findall(html):
                if "://" in ref or ref.startswith("//"):
                    continue
                rel = ref.split("?", 1)[0].split("#", 1)[0]
                # Vite serves "/x" from the project root, which is the html's directory.
                cands = [join(here, rel.lstrip("/"))]
                if rel.startswith("/"):
                    cands.append(norm(rel.lstrip("/")))
                for base in cands:
                    hit = resolve_file(base, files) if base is not None else None
                    if hit:
                        found.add(hit)
                        break
        return found
