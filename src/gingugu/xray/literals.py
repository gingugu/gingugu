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

DESIGN SKELETON (board d5639dd5): the signatures and constants below are the
contract the tests pin; the bodies are not written yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

MIN_LEN = 3
MAX_LEN = 80
MAX_LITERALS = 3  # per edit
TOO_COMMON = 25  # consumer files above which the note gives a count, not names
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


class Found(NamedTuple):
    """Where a literal is still named: ``(path, line)`` best-first, and the file total."""

    hits: list[tuple[str, int]]
    files: int


def qualifies(literal: str) -> bool:
    """A literal worth a sweep: identifier-shaped (``[A-Za-z_][A-Za-z0-9_.-]*``),
    ``MIN_LEN``..``MAX_LEN`` long, not in ``STOP`` (case-insensitive)."""
    raise NotImplementedError


def changed(olds: list[str], news: list[str]) -> list[str]:
    """Quoted literals (``"x"`` or ``'x'``) in the old strings that no new string
    still quotes, qualifying ones only, first seen first, each once, at most
    ``MAX_LITERALS``."""
    raise NotImplementedError


def consumers(root: Path, literal: str, exclude: str) -> Found:
    """The files still naming ``literal`` as ``"x"``, ``'x'``, `` `x` `` or ``@x``
    anywhere, or as a ``x:`` key in a non-code file - each bounded so ``x`` is
    not a prefix or suffix of a longer word. Skips ``exclude`` (the edited file),
    generated files and lockfiles. Ordered tests, then docs, then other non-code,
    then code; by path within each. Raises ``XrayError`` on a git failure or
    timeout."""
    raise NotImplementedError


def lines(payload: dict, root: Path, rel: str, state_dir: Path, session_id: str) -> list[str]:
    """The note's lines for this edit, one per removed literal with consumers,
    each literal claimed once per (session, repo). Never raises."""
    raise NotImplementedError
