"""Parse a tool argument against a controlled vocabulary.

One place for the error wording, so every enum-typed argument on the write
surface rejects a bad value the same way: naming the argument, the value, and
every value it would have accepted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

E = TypeVar("E", bound=StrEnum)


def parse_choice(enum_cls: type[E], value: str | None, field: str) -> E | None:
    """``value`` as a member of ``enum_cls``; None passes through.

    Raises ``ValueError`` listing the vocabulary when ``value`` is not in it.
    """
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        raise ValueError(
            f"invalid {field} {value!r}; expected one of {[m.value for m in enum_cls]}"
        ) from None


def parse_clearable(enum_cls: type[E], value: str | None, field: str) -> E | str | None:
    """Like ``parse_choice``, but ``""`` passes through as the clear signal.

    Same convention as ``metadata`` on ``memory_update``: None leaves the field
    unchanged, ``""`` clears it.
    """
    if value == "":
        return ""
    return parse_choice(enum_cls, value, field)
