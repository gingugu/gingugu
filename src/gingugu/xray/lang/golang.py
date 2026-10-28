"""Go: parsed with tree-sitter, resolved through ``go.mod``; imports name packages.

STUB - lists files only; the adapter is built against tests/test_xray_lang_go.py.
"""

from __future__ import annotations

from ..model import FileInfo, LangResult


class GoAdapter:
    name = "go"
    extensions = (".go",)
    manifests = ("go.mod",)

    def extract(self, sources: dict[str, bytes], manifests: dict[str, bytes]) -> LangResult:
        out = LangResult()
        for rel in sources:
            out.files.append(FileInfo(rel, rel.endswith("_test.go"), False, self.name))
        return out


ADAPTER = GoAdapter()
