"""X23 mentions: hosts, routes, resource-shaped names and sha citations."""

from __future__ import annotations

import re

from .mentions_sink import Sink
from .model import CITE, HOST, NAME, ROUTE, norm_host, norm_repo, norm_route

MAX_NAMES = 20_000
_URL = re.compile(r"\b(?:https?|wss?|grpcs?)://([A-Za-z0-9.-]+(?::\d{1,5})?)([^\s\"'`<>)\],;]*)")
_QUOTE = re.compile(r"[\"'`]")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,126}[A-Za-z0-9]")
_KV_HOST = re.compile(
    r"^\s*(?:-\s+)?(?:export\s+)?[\"']?([A-Za-z_][\w.-]*)[\"']?\s*[:=]\s*([\"']?)"
    r"([A-Za-z0-9*][^\s\"'#,;]*)"
)
_KV_VALUE = re.compile(
    r"^\s*(?:-\s+)?(?:export\s+)?[\"']?[\w.-]+[\"']?\s*[:=]\s*([^\s\"'#]+)\s*(?:#.*)?$"
)
_CITE = re.compile(r"(?<![\w./-])([\w.-]+(?:/[\w.-]+)?)@([0-9a-f]{7,40})(?!\w|\.\w)")
_CITE_PATH = re.compile(r"[\s(:,]+([\w./@-]+)")
_EXT = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def literals(line: str) -> list[str]:
    """Quoted string literals (", ', `) on one line, each at most 500 chars."""
    out: list[str] = []
    pos = 0
    while True:
        m = _QUOTE.search(line, pos)
        if not m:
            return out
        i = m.start()
        j = line.find(line[i], i + 1, i + 502)
        if j < 0:
            pos = i + 1
        else:
            out.append(line[i + 1 : j])
            pos = j + 1


def _route_in(lit: str) -> str | None:
    if lit.startswith("/"):
        return None if lit.startswith("//") else norm_route(lit)
    for k, ch in enumerate(lit):
        if ch in "})" and lit.startswith("/", k + 1):
            return norm_route(lit[k + 1 :])
    return None


def _name_shaped(value: str) -> bool:
    if not _NAME.fullmatch(value):
        return False
    return len(value) >= 6 or any(c in value for c in "-_.")


def _add_name(sink: Sink, value: str, path: str, line: int) -> None:
    if sink.names < MAX_NAMES and _name_shaped(value):
        if sink.add(NAME, value, path, line):
            sink.names += 1


def scan_net(path: str, lines: list[str], sink: Sink, code: bool) -> None:
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        n = i + 1
        for m in _URL.finditer(ln):
            sink.add(HOST, norm_host(m.group(1)), path, n)
            rest = m.group(2)
            if rest and not re.split(r"[?#]", rest, maxsplit=1)[0].endswith(".git"):
                route = norm_route(rest)
                if route and route != "/":
                    sink.add(ROUTE, route, path, n)
        kv = _KV_HOST.match(ln) if "host" in ln.lower() else None
        if kv and "host" in kv.group(1).lower() and (kv.group(2) or not code):
            sink.add(HOST, norm_host(kv.group(3)), path, n)
        for lit in literals(ln):
            route = _route_in(lit)
            if route and route != "/":
                sink.add(ROUTE, route, path, n)
            _add_name(sink, lit, path, n)
        if not code:
            m = _KV_VALUE.match(ln)
            if m:
                _add_name(sink, m.group(1), path, n)


def scan_cites(path: str, lines: list[str], sink: Sink) -> None:
    for i, ln in enumerate(lines):
        if "@" not in ln:
            continue
        for m in _CITE.finditer(ln):
            sha = m.group(2)
            if not any(c in "abcdef" for c in sha):
                continue
            target = ""
            p = _CITE_PATH.match(ln, m.end())
            if p:
                tok = p.group(1).rstrip(".,;:)")
                if _EXT.search(tok):
                    target = tok
            sink.add(CITE, norm_repo(m.group(1)), path, i + 1, sha, target)
