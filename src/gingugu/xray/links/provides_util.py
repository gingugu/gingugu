"""Shared helpers for the X23 provider extractors: capped lines, scalars, JSON."""

from __future__ import annotations

import json
import re

LINE_CAP = 4000  # a regex never sees more of a line than this: bounds every scan
Hit = tuple[str, str, int]  # (kind, raw key, 1-based line)

_URL_HOST = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?([^/\s?#]+)")


def lines_of(text: str) -> list[str]:
    return [ln[:LINE_CAP].rstrip("\r") for ln in text.split("\n")]


def scalar(value: str) -> str:
    """A YAML/HCL scalar: unquoted, with a trailing ``# comment`` cut off."""
    v = value.strip()
    if v[:1] in ('"', "'"):
        end = v.find(v[0], 1)
        return v[1:end] if end > 0 else v[1:]
    cut = v.find(" #")
    return (v if cut < 0 else v[:cut]).strip()


def url_host(url: str) -> str:
    m = _URL_HOST.match(url.strip())
    return m.group(1).rpartition("@")[2] if m else ""


def json_openapi(text: str) -> dict | None:
    """The parsed document when ``text`` is a JSON OpenAPI/Swagger spec."""
    if not text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except Exception:  # malformed or absurdly nested: contributes nothing
        return None
    if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
        return data
    return None


def is_yaml_openapi(lines: list[str]) -> bool:
    return any(re.match(r"^(?:openapi|swagger)\s*:", ln) for ln in lines)


def first_line_with(lines: list[str], needle: str) -> int:
    for i, ln in enumerate(lines, 1):
        if needle in ln:
            return i
    return 1
