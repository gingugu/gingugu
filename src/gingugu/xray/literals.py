"""X-ray after an edit: the text readers of a quoted literal the edit removed.

Nothing imports a data contract. Renaming the key of an emitted JSON line
(``"status"`` -> ``"outcome"``) leaves its readers - tests asserting
``line["status"]``, runbooks naming the field, monitor queries on ``@status:`` -
invisible to the import graph. So when an ``Edit``/``MultiEdit`` removes a quoted
literal, the post-edit note lists the files that still name it, labelled as text
matches, never as import edges.

Zero-config: one ``git grep -F`` per literal over the checkout as it is, then an
exact boundary check in Python. Bounded: at most ``MAX_LITERALS`` per edit, each
named once per session, and a literal named in more than ``TOO_COMMON`` files is
reported as a count instead of a list.

Board d5639dd5.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from . import hook_common, scan
from .hook_common import _show
from .links import generated
from .links.model import CODE_EXTS, LOCKFILES, is_doc, is_test
from .scan import XrayError

MIN_LEN = 3
MAX_LEN = 80
MAX_LITERALS = 3  # per edit
# Consumer files above which the note gives a count, not names. Measured on
# gingugu over 150 sampled quoted keys: p50 5, p90 28, p95 44.
TOO_COMMON = 40
NAMED = 5  # consumer locations named per literal
GIT_TIMEOUT_S = 1.0  # per literal; a slow grep drops the line, never the note
LABEL = "Text matches, not import edges"

# Values, not keys: a literal any file names for reasons of its own.
STOP = frozenset(
    {
        "utf-8", "utf8", "ascii", "true", "false", "none", "null", "yes",
        "get", "post", "put", "patch", "delete", "head",
        "replace", "strict", "ignore", "self", "main", "__main__",
    }
)  # fmt: skip

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_QUOTED = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'")
_NAME = r"([A-Za-z_][A-Za-z0-9_]*)"
# Python keyword argument: ``=`` (not ``==``) right after the name, which follows
# ``(`` or ``,``. An Edit's old_string need not include the ``(``, so a fragment
# that opens on ``name=`` counts too; ``status = 1`` (spaced) stays an assignment.
_PY_KWARG = re.compile(rf"(?:(?<=[(,])\s*|\A\s*){_NAME}=(?!=)")
# TS/JS object key: ``name:`` after ``{`` or ``,``, or leading a line.
_JS_KEY = re.compile(rf"(?:(?<=[{{,])|^)\s*{_NAME}\s*:(?!:)", re.MULTILINE)
_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


class Found(NamedTuple):
    """Where a literal is still named: ``(path, line)`` best-first, and the file total."""

    hits: list[tuple[str, int]]
    files: int


def qualifies(literal: str) -> bool:
    """A literal worth a sweep: identifier-shaped (``[A-Za-z_][A-Za-z0-9_.-]*``),
    ``MIN_LEN``..``MAX_LEN`` long, not in ``STOP`` (case-insensitive)."""
    return (
        MIN_LEN <= len(literal) <= MAX_LEN
        and _IDENT.fullmatch(literal) is not None
        and literal.lower() not in STOP
    )


def _quoted(texts: list[str]) -> list[str]:
    """Every quoted string in the texts, quotes stripped, first seen first."""
    return [m[1:-1] for t in texts for m in _QUOTED.findall(t)]


def _removed(found: list[str], kept: set[str]) -> list[str]:
    out: list[str] = []
    for lit in found:
        if lit not in kept and lit not in out and qualifies(lit):
            out.append(lit)
    return out[:MAX_LITERALS]


def changed(olds: list[str], news: list[str]) -> list[str]:
    """Quoted literals (``"x"`` or ``'x'``) in the old strings that no new string
    still quotes, qualifying ones only, first seen first, each once, at most
    ``MAX_LITERALS``."""
    return _removed(_quoted(olds), set(_quoted(news)))


def _keys(texts: list[str], rel: str) -> list[str]:
    low = rel.lower()
    if low.endswith(".py"):
        pattern = _PY_KWARG
    elif low.endswith(_JS_EXTS):
        pattern = _JS_KEY
    else:
        return []
    return [m.group(1) for t in texts for m in pattern.finditer(t)]


def changed_bare(olds: list[str], news: list[str], rel: str) -> list[str]:
    """Unquoted keys the old strings name and no new string still names, by the
    edited file's language, so a form one language uses is never read in another:

    - Python (``.py``): a keyword argument in a call, ``f(a, status=x)`` and
      ``dict(status=x)`` - ``=`` after ``(`` or ``,``, never ``==``. A plain
      assignment ``status = 1`` is a variable, not a key.
    - TS/JS (``.ts .tsx .js .jsx .mjs .cjs``): an object-literal key,
      ``{status: x}`` or ``status:`` leading a line inside one.

    Any other language names none. Same noise rule (``qualifies``), first seen
    first, each once, at most ``MAX_LITERALS``."""
    return _removed(_keys(olds, rel), set(_keys(news, rel)))


def _forms(literal: str, bare: bool) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]:
    """(forms read in code, forms read in tests and non-code), strongest first:
    a quoted or backticked name, a ``x:`` key, then ``@x``; unquoted adds ``x=``/``.x``."""
    e = re.escape(literal)
    lead, tail = r"(?<![\w.@-])", r"(?![\w-])"
    quoted = re.compile(rf"\"{e}\"|'{e}'|`{e}`")
    at = re.compile(rf"@{e}{tail}")
    key = re.compile(rf"{lead}{e}\s*:")
    if not bare:
        return [quoted, at], [quoted, key, at]
    extra = re.compile(rf"{lead}{e}\s*=(?!=)|(?<=\.){e}{tail}")
    return [quoted, at], [quoted, key, extra, at]


def _rank(text: str, forms: list[re.Pattern[str]]) -> int | None:
    """Index of the strongest form the line reads in, None if it reads in none."""
    return next((i for i, f in enumerate(forms) if f.search(text)), None)


def _group(path: str) -> int:
    """0 test or fixture, 1 doc, 2 other non-code, 3 code."""
    if is_test(path):
        return 0
    if is_doc(path):
        return 1
    return 3 if path.lower().endswith(CODE_EXTS) else 2


def _skipped(path: str, exclude: str) -> bool:
    return (
        path == exclude or path.rsplit("/", 1)[-1].lower() in LOCKFILES or generated.by_name(path)
    )


def _grep(root: Path, literal: str) -> list[tuple[str, int, str]]:
    """``(path, line, text)`` for every line naming ``literal``, tracked or not."""
    proc = scan.git(
        root, "grep", "-n", "-I", "-F", "--untracked", "-z", "-e", literal,
        timeout=GIT_TIMEOUT_S,
    )  # fmt: skip
    if proc.returncode not in (0, 1):  # 1 is "no match"
        raise XrayError(f"git grep failed in {root}")
    out: list[tuple[str, int, str]] = []
    data, i = proc.stdout, 0
    while i < len(data):  # -z: path NUL line NUL text LF
        a = data.find(b"\0", i)
        b = data.find(b"\0", a + 1) if a >= 0 else -1
        if b < 0:
            break
        end = data.find(b"\n", b + 1)
        end = len(data) if end < 0 else end
        try:
            n = int(data[a + 1 : b])
        except ValueError:
            break
        text = data[b + 1 : end].decode("utf-8", "replace")
        out.append((data[i:a].decode("utf-8", "replace"), n, text))
        i = end + 1
    return out


def consumers(root: Path, literal: str, exclude: str, *, bare: bool = False) -> Found:
    """The files still naming ``literal``, each match bounded so ``x`` is not a
    prefix or suffix of a longer word. Skips ``exclude`` (the edited file),
    generated files and lockfiles. Ordered tests, then docs, then other non-code,
    then code; by path within each. Raises ``XrayError`` on a git failure or
    timeout.

    Quoted (``bare=False``): ``"x"``, ``'x'``, `` `x` `` or ``@x`` anywhere, or a
    ``x:`` key in a non-code file.

    Unquoted (``bare=True``): the quoted forms plus ``x=``, ``x:`` and ``.x``, but
    only in test files (``links.model.is_test``) and non-code files. Non-test code
    is left to the symbol graph, which already follows a renamed keyword."""
    in_code, loose = _forms(literal, bare)
    found: dict[tuple[str, int], tuple[int, int]] = {}  # (path, line) -> (group, rank)
    for path, n, text in _grep(root, literal):
        group = _group(path)
        if _skipped(path, exclude) or (group == 3 and bare):
            continue
        rank = _rank(text, in_code if group == 3 else loose)
        if rank is not None:
            found[path, n] = (group, rank)
    if not bare:  # one location per file: its strongest form, then its first line
        best: dict[str, tuple[int, int, int]] = {}
        for (path, n), (group, rank) in found.items():
            best[path] = min(best.get(path, (rank, n, group)), (rank, n, group))
        found = {(p, n): (g, r) for p, (r, n, g) in best.items()}
    hits = sorted((g, p, n) for (p, n), (g, _) in found.items())
    return Found([(p, n) for _, p, n in hits], len({p for _, p, _ in hits}))


def _edits(payload: dict) -> tuple[list[str], list[str]] | None:
    """(old strings, new strings) of an Edit or MultiEdit; None for anything else."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    if payload.get("tool_name") == "Edit":
        rows = [tool_input]
    elif payload.get("tool_name") == "MultiEdit" and isinstance(tool_input.get("edits"), list):
        rows = [e for e in tool_input["edits"] if isinstance(e, dict)]
    else:
        return None
    olds = [r.get("old_string") for r in rows]
    news = [r.get("new_string") for r in rows]
    return [o for o in olds if isinstance(o, str) and o], [n for n in news if isinstance(n, str)]


def _line(literal: str, found: Found, bare: bool) -> str:
    what = f"`{literal}`" + (" (unquoted key)" if bare else "")
    head = f"- {LABEL}: {what} removed by this edit"
    tail = " (read in tests, docs and config only)" if bare else ""
    if found.files > TOO_COMMON:
        return f"{head} is named in {found.files} files - too common to list{tail}."
    named = ", ".join(f"{_show(p)}:{n}" for p, n in found.hits[:NAMED])
    more = f" (+{len(found.hits) - NAMED} more)" if len(found.hits) > NAMED else ""
    s = "" if found.files == 1 else "s"
    return f"{head} is still named in {found.files} file{s} - {named}{more}{tail}."


def lines(payload: dict, root: Path, rel: str, state_dir: Path, session_id: str) -> list[str]:
    """The note's lines for this edit, one per removed literal with consumers:
    quoted literals first, then unquoted keys (``changed_bare``), a key found both
    ways swept once as quoted, at most ``MAX_LITERALS`` in all. An unquoted key's
    line says ``unquoted key`` and that only tests, docs and config were read.
    Each literal is claimed once per (session, repo). Never raises."""
    try:
        edits = _edits(payload)
        if edits is None or not edits[0]:
            return []
        olds, news = edits
        plan = [(lit, False) for lit in changed(olds, news)]
        quoted = {lit for lit, _ in plan}
        plan += [(k, True) for k in changed_bare(olds, news, rel) if k not in quoted]
        out: list[str] = []
        for lit, bare in plan[:MAX_LITERALS]:
            try:
                found = consumers(root, lit, rel, bare=bare)
            except XrayError:
                continue  # a slow or failed grep drops this line, never the note
            claim = hook_common.claim_path(state_dir, "xray-literal", session_id, root, lit)
            if found.hits and hook_common._claim(claim):
                out.append(_line(lit, found, bare))
        return out
    except Exception:  # noqa: BLE001 - a hook never breaks a session
        return []
