"""tree-sitter side of the TS/JS adapter: one file in, its import specifiers out.

tree-sitter is imported lazily and each grammar's parser is cached at module
level, so a Python-only repo never loads one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_PARSERS: dict[str, object] = {}

# kind of the specifier: static import/require, type-only, or dynamic import()
MODULE, TYPE, LAZY = "module", "type_checking", "lazy"


@dataclass
class Imports:
    specs: list[tuple[str, str]] = field(default_factory=list)  # (specifier, kind)
    dynamic: int = 0
    has_error: bool = False


def _parser(path: str):
    if path.endswith(".tsx"):
        key = "tsx"
    elif path.endswith((".ts", ".mts", ".cts")):
        key = "ts"
    else:
        key = "js"
    if key not in _PARSERS:
        import tree_sitter as ts

        if key == "js":
            import tree_sitter_javascript as grammar

            lang = grammar.language()
        else:
            import tree_sitter_typescript as grammar

            lang = grammar.language_tsx() if key == "tsx" else grammar.language_typescript()
        _PARSERS[key] = ts.Parser(ts.Language(lang))
    return _PARSERS[key]


def _string_value(node) -> str:
    return b"".join(c.text for c in node.children if c.type == "string_fragment").decode(
        "utf-8", "replace"
    )


def _has_type_token(node) -> bool:
    return any(c.type == "type" for c in node.children)


def _call(node, out: Imports) -> None:
    fn = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if fn is None or args is None:
        return
    is_import = fn.type == "import"
    if not is_import and not (fn.type == "identifier" and fn.text == b"require"):
        return
    first = next(iter(args.named_children), None)
    if first is None:
        return
    if first.type == "string":
        out.specs.append((_string_value(first), LAZY if is_import else MODULE))
    else:
        out.dynamic += 1


def parse(path: str, data: bytes) -> Imports:
    """Every import-like specifier in ``data``, plus a count of computed ones."""
    out = Imports()
    tree = _parser(path).parse(data)
    out.has_error = tree.root_node.has_error
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        t = node.type
        if t in ("import_statement", "export_statement"):
            src = node.child_by_field_name("source")
            if src is not None and src.type == "string":
                kind = TYPE if _has_type_token(node) else MODULE
                out.specs.append((_string_value(src), kind))
        elif t == "import_require_clause":
            src = next((c for c in node.children if c.type == "string"), None)
            if src is not None:
                out.specs.append((_string_value(src), MODULE))
        elif t == "call_expression":
            _call(node, out)
        stack.extend(node.children)
    return out
