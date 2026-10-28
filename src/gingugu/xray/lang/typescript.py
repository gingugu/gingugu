"""TypeScript and JavaScript: parsed with tree-sitter, resolved by path.

STUB - lists files only; the adapter is built against tests/test_xray_lang_typescript.py.
"""

from __future__ import annotations

from ..model import FileInfo, LangResult

TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")
JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")


class TypeScriptAdapter:
    name = "typescript"
    extensions = TS_EXTENSIONS + JS_EXTENSIONS
    manifests = ("tsconfig*.json", "jsconfig*.json", "package.json", "index.html")

    def extract(self, sources: dict[str, bytes], manifests: dict[str, bytes]) -> LangResult:
        out = LangResult()
        for rel in sources:
            lang = "typescript" if rel.endswith(TS_EXTENSIONS) else "javascript"
            out.files.append(FileInfo(rel, False, False, lang))
        return out


ADAPTER = TypeScriptAdapter()
