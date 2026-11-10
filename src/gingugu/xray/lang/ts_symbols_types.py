"""What a TS/JS annotation or initialiser says about a value's class.

Used by the walk, which writes the references; the linker resolves them. A
reference is a ``(form, x, y)`` triple, with the forms

- ``n`` an instance of class ``x`` and ``a`` of class ``x.y`` (a namespace member);
- ``p`` a primitive or array, whose methods are not this repo's code;
- ``c`` whatever ``x()`` returns and ``d`` whatever ``x.y()`` returns;
- ``u`` a value of unknown type (it still shadows an import of the same name).

Every walk here is a loop, never recursion: a hostile file can nest as deep as it likes.
"""

from __future__ import annotations

Ref = tuple[str, str, str]

UNKNOWN: Ref = ("u", "", "")
PRIMITIVE: Ref = ("p", "", "")
_NULL = object()
_PRIM_TYPES = {"string", "number", "boolean", "bigint", "symbol"}
_NULLS = {"null", "undefined"}
# Not "object": an object literal can hold the repo's own methods, so it stays unknown.
_PRIM_VALUES = {"string", "template_string", "number", "array", "regex", "true", "false"}
_WRAPPERS = ("type_annotation", "parenthesized_type")


def text(node) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


def _leaf(node) -> object:
    """The reference of one non-union type, ``_NULL`` for null/undefined, or None."""
    t = node.type
    if t == "generic_type":
        node = node.child_by_field_name("name")
        t = node.type if node is not None else ""
    if t == "type_identifier":
        return "n", text(node), ""
    if t == "nested_type_identifier":
        mod, name = node.child_by_field_name("module"), node.child_by_field_name("name")
        if mod is not None and mod.type == "identifier" and name is not None:
            return "a", text(mod), text(name)
        return None
    if t == "predefined_type":
        return PRIMITIVE if text(node) in _PRIM_TYPES else None
    if t in ("array_type", "tuple_type"):
        return PRIMITIVE
    if t == "literal_type":
        return _NULL if text(node) in _NULLS else PRIMITIVE
    return None


def type_ref(node) -> Ref | None:
    """The reference an annotation (or a bare type node) names, else None.

    ``Store | null`` is ``Store``; a union of primitives is a primitive; any
    other union is not one class, so it names none."""
    leaves, stack = [], [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if n.type == "union_type" or n.type in _WRAPPERS:
            stack.extend(reversed(n.named_children))
        else:
            leaves.append(_leaf(n))
    refs = [r for r in leaves if r is not _NULL]
    if not refs or any(r is None for r in refs):
        return None
    if all(r == PRIMITIVE for r in refs):
        return PRIMITIVE
    return refs[0] if len(refs) == 1 else None  # type: ignore[return-value]


def value_ref(node) -> Ref | None:
    """The reference an initialiser implies: a literal, ``new X()``, ``x()``, ``x.y()``."""
    if node is None:
        return None
    t = node.type
    if t in _PRIM_VALUES:
        return PRIMITIVE
    if t in ("as_expression", "satisfies_expression") and node.named_children:
        return type_ref(node.named_children[-1])
    if t == "new_expression":
        return _callee_ref(node.child_by_field_name("constructor"), "n", "a")
    if t == "call_expression":
        return _callee_ref(node.child_by_field_name("function"), "c", "d")
    return None


def _callee_ref(fn, bare: str, member: str) -> Ref | None:
    if fn is None:
        return None
    if fn.type == "identifier":
        return bare, text(fn), ""
    if fn.type == "member_expression":
        obj, prop = fn.child_by_field_name("object"), fn.child_by_field_name("property")
        if obj is not None and obj.type == "identifier" and prop is not None:
            return member, text(obj), text(prop)
    return None
