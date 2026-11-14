"""Parsing a grant spec: ``"name=level,name=level"`` into a grant map."""

from __future__ import annotations

from .grants import LEVELS


def parse_grant_spec(spec: str) -> dict[str, str]:
    """Parse ``"name=level,name=level"`` (name may be ``*``) into a grant map."""
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, level = part.partition("=")
        name, level = name.strip(), level.strip()
        if not sep:
            raise ValueError(f"expected name=level, got {part!r}")
        if not name:
            raise ValueError(f"empty namespace name in {part!r}")
        if level not in LEVELS:
            raise ValueError(f"level for {name!r} must be one of {', '.join(LEVELS)}")
        if name in out:
            raise ValueError(f"namespace {name!r} listed twice")
        out[name] = level
    if not out:
        raise ValueError("no namespace grants given")
    return out
