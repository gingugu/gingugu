"""X23 mentions: repos a CI job checks out, reuses, clones or acts on."""

from __future__ import annotations

import re

from .mentions_sink import Sink, repo_from_url, unquote
from .model import CI, SUBMODULE, norm_repo

_REPOSITORY = re.compile(r"^(\s*)(-\s+)?repository:\s*(\S+)")
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*[\"']?([^\s\"'#]+)")
_USES_REF = re.compile(r"^([\w.-]+)/([\w.-]+)(?:/[^@\s]*)?@(\S+)$")
_PROJECT = re.compile(r"^(\s*)(-\s+)?project:\s*(\S+)")
_OWNER_NAME = re.compile(r"[\w.-]+/[\w.-]+")
_CLONE = re.compile(r"\bgit\s+clone\b(.*)$")
_GH = re.compile(r"(?:^|\s)(?:--repo|-R)(?:=|\s+)([\w.-]+/[\w.-]+)")
_URL_TOKEN = re.compile(r"^(?:(?:https?|ssh|git)://|[\w.-]+@[\w.-]+:)")
_URL_LINE = re.compile(r"^\s*url\s*=\s*(\S+)")
_KEYVAL = re.compile(r"(?:-\s+)?(\w+):\s*(\S+)")


def _sibling(lines: list[str], i: int, indent: int, dash: bool, names: tuple, span: int) -> str:
    """The value of a ``ref:``/``branch:`` key in the same YAML mapping, within
    ``span`` lines below line index ``i``."""
    for j in range(i + 1, min(len(lines), i + 1 + span)):
        ln = lines[j]
        body = ln.lstrip()
        if not body:
            continue
        ind = len(ln) - len(body)
        if ind < indent or (ind == indent and (dash or body.startswith("-"))):
            return ""
        m = _KEYVAL.match(body)
        if m and m.group(1) in names:
            return unquote(m.group(2))
    return ""


def scan_ci_yaml(path: str, lines: list[str], sink: Sink) -> None:
    for i, ln in enumerate(lines):
        if not ln or ("repository:" not in ln and "uses:" not in ln and "project:" not in ln):
            continue
        n = i + 1
        m = _REPOSITORY.match(ln)
        if m:
            val = unquote(m.group(3))
            if "$" not in val and _OWNER_NAME.fullmatch(val):
                ref = _sibling(lines, i, len(m.group(1)), bool(m.group(2)), ("ref",), 6)
                sink.add(CI, norm_repo(val), path, n, ref)
            continue
        m = _USES.match(ln)
        if m:
            val = m.group(1)
            u = None if val.startswith(("./", "docker://")) else _USES_REF.match(val)
            if u:
                sink.add(CI, norm_repo(f"{u.group(1)}/{u.group(2)}"), path, n, u.group(3))
            continue
        m = _PROJECT.match(ln)
        if m:
            val = unquote(m.group(3))
            parts = [p for p in val.split("/") if p]
            if "$" not in val and len(parts) >= 2:
                ref = _sibling(lines, i, len(m.group(1)), bool(m.group(2)), ("ref", "branch"), 4)
                sink.add(CI, norm_repo("/".join(parts[-2:])), path, n, ref)


def scan_clones(path: str, lines: list[str], sink: Sink) -> None:
    """``git clone`` and ``gh --repo`` in CI files and shell scripts."""
    for i, ln in enumerate(lines):
        if "git" not in ln and "gh" not in ln:
            continue
        m = _CLONE.search(ln)
        if m:
            toks = m.group(1).split()
            branch = ""
            url = None
            for k, tok in enumerate(toks):
                tok = unquote(tok)
                if tok in ("-b", "--branch") and k + 1 < len(toks):
                    branch = unquote(toks[k + 1])
                elif tok.startswith("--branch="):
                    branch = tok.split("=", 1)[1]
                elif url is None and _URL_TOKEN.match(tok):
                    url = tok
            sink.add(CI, repo_from_url(url) if url else None, path, i + 1, branch)
        if re.search(r"\bgh\s", ln):
            for g in _GH.finditer(ln):
                sink.add(CI, norm_repo(g.group(1)), path, i + 1)


def scan_submodules(path: str, lines: list[str], sink: Sink) -> None:
    for i, ln in enumerate(lines):
        m = _URL_LINE.match(ln)
        if m:
            sink.add(SUBMODULE, repo_from_url(m.group(1)), path, i + 1)
