"""Path candidates for a TS/JS module: which repo file does a specifier name?"""

from __future__ import annotations

import posixpath

CODE_EXTS = (".ts", ".tsx", ".d.ts", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
# ESM imports name the emitted file (./x.js); the source is its TS twin.
_ESM = {
    ".js": (".ts", ".tsx", ".d.ts"),
    ".jsx": (".tsx",),
    ".mjs": (".mts", ".d.mts"),
    ".cjs": (".cts", ".d.cts"),
}
# Non-code files a bundler lets you import; they are never graph nodes.
ASSET_EXTS = frozenset(
    ".css .scss .sass .less .styl .json .svg .png .jpg .jpeg .gif .webp .avif .ico .bmp"
    " .woff .woff2 .ttf .otf .eot .mp3 .mp4 .webm .wav .ogg .wasm .md .mdx .txt .html"
    " .yaml .yml .toml .csv .xml .graphql .gql .pdf".split()
)


def norm(path: str) -> str | None:
    """Normalise a repo-relative path; None when it climbs out of the repo."""
    p = posixpath.normpath(path)
    if p == ".":
        return ""
    return None if p == ".." or p.startswith(("../", "/")) else p


def join(base: str, rel: str) -> str | None:
    return norm(posixpath.join(base, rel))


def strip_query(spec: str) -> str:
    for mark in ("?", "#"):
        spec = spec.split(mark, 1)[0]
    return spec


def is_relative(spec: str) -> bool:
    return spec in (".", "..") or spec.startswith(("./", "../"))


def asset_ext(spec: str) -> str:
    """The non-code extension of a specifier's last segment, '' when it has none."""
    name = spec.rsplit("/", 1)[-1]
    if "." not in name.lstrip(".") or name.endswith(CODE_EXTS):
        return ""
    return posixpath.splitext(name)[1].lower()


def resolve_file(base: str, files: set[str] | dict[str, bytes]) -> str | None:
    """The source file ``base`` names, in TypeScript's order; None when there is none.

    Exact path, the ESM ``.js`` -> ``.ts`` twin, ``base`` + each extension, then
    ``base/index`` + each extension.
    """
    if base in files:
        return base
    stem, ext = posixpath.splitext(base)
    for twin in _ESM.get(ext, ()):
        if stem + twin in files:
            return stem + twin
    for e in CODE_EXTS:
        if base + e in files:
            return base + e
    index = f"{base}/index" if base else "index"
    for e in CODE_EXTS:
        if index + e in files:
            return index + e
    return None
