"""TypeScript and JavaScript: parsed with tree-sitter, resolved by path.

Edges come from static ``import``/``export ... from``/``require`` (``module``),
``import type`` (``type_checking``) and ``import()`` (``lazy``). Specifiers resolve
relative to the file, through the nearest tsconfig/jsconfig (``paths``,
``baseUrl``, ``extends``) and through in-repo workspace packages named by a
``package.json``. Entries are package.json ``main``/``module``/``bin``/``exports``
and ``index.html`` script tags. Configs are data: nothing is run, nothing is read.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ..model import Edge, FileInfo, LangResult
from .ts_config import Manifests
from .ts_parse import LAZY, MODULE, TYPE, parse
from .ts_paths import ASSET_EXTS, asset_ext, is_relative, strip_query
from .ts_resolve import Resolver

TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")
JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")

_STRENGTH = {TYPE: 1, LAZY: 2, MODULE: 3}
MAX_SPEC = 1024
_TEST_FILE = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$")
_TEST_DIRS = ("__tests__", "tests", "test")


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return any(part in _TEST_DIRS for part in p.parts[:-1]) or bool(_TEST_FILE.search(p.name))


class TypeScriptAdapter:
    name = "typescript"
    extensions = TS_EXTENSIONS + JS_EXTENSIONS
    manifests = ("tsconfig*.json", "jsconfig*.json", "package.json", "index.html")

    def extract(self, sources: dict[str, bytes], manifests: dict[str, bytes]) -> LangResult:
        out = LangResult()
        mf = Manifests(manifests, out.skipped)
        resolver = Resolver(sources, mf)
        entries = mf.entries(sources)
        for rel, data in sources.items():
            lang = "typescript" if rel.endswith(TS_EXTENSIONS) else "javascript"
            out.files.append(FileInfo(rel, is_test(rel), rel in entries, lang))
            imports = parse(rel, data)
            if imports.has_error:
                out.parse_errors.append(rel)
            out.dynamic += imports.dynamic
            found: dict[str, tuple[str, bool]] = {}
            for spec, kind in imports.specs:
                self._resolve(rel, spec, kind, resolver, out, found)
            out.edges.extend(Edge(rel, dst, k, c) for dst, (k, c) in found.items())
        return out

    @staticmethod
    def _resolve(rel, spec, kind, resolver, out, found) -> None:
        spec = strip_query(spec)
        if not spec:
            return
        if len(spec) > MAX_SPEC:  # no real module name is this long; never resolved
            out.external += 1
            return
        certain = True
        if is_relative(spec):
            dst = resolver.relative(rel, spec)
            if dst is None:
                if not asset_ext(spec):
                    out.unresolved += 1
                return
        else:
            if asset_ext(spec) in ASSET_EXTS:
                return
            hit = resolver.bare(rel, spec)
            if hit is None:
                out.external += 1
                return
            dst, certain = hit
        if dst == rel:
            return
        old = found.get(dst)
        if old is None or _STRENGTH[kind] > _STRENGTH[old[0]]:
            found[dst] = (kind, certain)


ADAPTER = TypeScriptAdapter()
