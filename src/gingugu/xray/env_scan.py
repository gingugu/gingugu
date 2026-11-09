"""X7a: what one file says about environment variables - names and lines only.

Pure text in, ``(reads, sets)`` out, each ``(NAME, 1-based line)`` in line order.
Only literal names in known forms count; a name built at run time is missed. A
real dotenv file (``.env``, ``.env.local``, ``.envrc``, ...) is never inspected:
it holds secrets, and only the example templates are read. No value is kept.
"""

from __future__ import annotations

import re

NAME = r"[A-Z][A-Z0-9_]{1,63}"
_NAME = re.compile(NAME)
_Q = r"""["']"""
_END = r"(?![A-Za-z0-9_])"

Pairs = list[tuple[str, int]]

_PY_READS = (
    re.compile(rf"\benviron\[\s*{_Q}({NAME}){_Q}\s*\](?!\s*=(?!=))"),
    re.compile(rf"\benviron\.get\(\s*{_Q}({NAME}){_Q}"),
    re.compile(rf"\bos\.getenv\(\s*{_Q}({NAME}){_Q}"),
)
_PY_SETS = (
    re.compile(rf"\benviron\[\s*{_Q}({NAME}){_Q}\s*\]\s*=(?!=)"),
    re.compile(rf"\benviron\.setdefault\(\s*{_Q}({NAME}){_Q}"),
    re.compile(rf"\bmonkeypatch\.setenv\(\s*{_Q}({NAME}){_Q}"),
)
_JS_READS = (
    re.compile(rf"\bprocess\.env\.({NAME}){_END}"),
    re.compile(rf"\bprocess\.env\[\s*{_Q}({NAME}){_Q}\s*\]"),
    re.compile(rf"\bimport\.meta\.env\.({NAME}){_END}"),
)
_GO_READS = (re.compile(rf'\bos\.(?:Getenv|LookupEnv)\(\s*"({NAME})"'),)
_SH_SETS = (re.compile(rf"^\s*export\s+({NAME})="),)
_SH_READS = (re.compile(rf"\$\{{({NAME}):[-?]"),)
_DOCKER_SETS = (re.compile(rf"^(?:ENV|ARG)\s+({NAME})(?:=|\s|$)"),)
_TEMPLATE_SETS = (re.compile(rf"^\s*(?:export\s+)?({NAME})="),)
_OPEN = re.compile(r"^(\s*)(?:-\s+)?(?:env|environment):\s*$")
_YAML_SETS = (
    re.compile(rf"^-\s+name:\s*{_Q}?({NAME}){_Q}?(?:\s|$)"),
    re.compile(rf"^-\s+({NAME})="),
    re.compile(rf"^(?:-\s+)?({NAME}):(?:\s|$)"),
)

TEMPLATES = (".env.example", ".env.sample", ".env.template", ".env.dist", ".env.defaults")
_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_SH_EXTS = (".sh", ".bash", ".zsh")
_YAML_EXTS = (".yml", ".yaml")


def _base(rel: str) -> str:
    return rel.rsplit("/", 1)[-1]


def kind(rel: str) -> str | None:
    """Which reader handles ``rel``, by name alone; None for anything else, and for
    every real dotenv file, so the caller never opens one."""
    base = _base(rel)
    if base.startswith(".env"):
        return "template" if base in TEMPLATES else None
    if base == "Dockerfile" or base.startswith("Dockerfile."):
        return "docker"
    ext = base[base.rfind(".") :].lower() if "." in base else ""
    if ext == ".py":
        return "py"
    if ext in _JS_EXTS:
        return "js"
    if ext == ".go":
        return "go"
    if ext in _SH_EXTS:
        return "sh"
    if ext in _YAML_EXTS:
        return "yaml"
    return None


def _hits(patterns: tuple, line: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(1)) for p in patterns for m in p.finditer(line)]


def _add(out: Pairs, found: list[tuple[int, str]], number: int) -> None:
    for _, name in sorted(found):
        if (name, number) not in out:
            out.append((name, number))


def _yaml(text: str) -> Pairs:
    sets: Pairs = []
    block: int | None = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # neither blank nor comment lines close a block
        indent = len(line) - len(line.lstrip(" "))
        if block is not None and indent <= block:
            block = None
        if block is not None:
            _add(sets, _hits(_YAML_SETS, stripped), number)
            continue
        opened = _OPEN.match(line)
        if opened:
            block = len(opened.group(1))
    return sets


def scan_text(rel: str, text: str) -> tuple[Pairs, Pairs]:
    """(reads, sets) of ``text`` as the file ``rel``."""
    which = kind(rel)
    reads: Pairs = []
    sets: Pairs = []
    if which is None:
        return reads, sets
    if which == "yaml":
        return reads, _yaml(text)
    read_pats, set_pats = {
        "py": (_PY_READS, _PY_SETS),
        "js": (_JS_READS, ()),
        "go": (_GO_READS, ()),
        "sh": (_SH_READS, _SH_SETS),
        "docker": ((), _DOCKER_SETS),
        "template": ((), _TEMPLATE_SETS),
    }[which]
    for number, line in enumerate(text.splitlines(), 1):
        if which == "template" and line.lstrip().startswith("#"):
            continue
        _add(reads, _hits(read_pats, line), number)
        _add(sets, _hits(set_pats, line), number)
    return reads, sets


def valid_name(value: object) -> bool:
    return isinstance(value, str) and _NAME.fullmatch(value) is not None
