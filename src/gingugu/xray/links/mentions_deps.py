"""X23 mentions: packages a manifest depends on, and repos it pins by git URL."""

from __future__ import annotations

import json
import re
import tomllib

from .mentions_sink import Sink, split_lines
from .model import DEP, MAX_FILE_BYTES, REPO_DEP, norm_package, norm_repo

_GIT = re.compile(
    r"git\+(?:https?|ssh)://[^/\s]+/([\w.-]+)/([\w.-]+?)(?:\.git)?"
    r"(?:@([^\s#;\"']+))?(?=[#\s;\"']|$)"
)
_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")
_NPM_GH = re.compile(r"^(?:github:)?([\w.-]+)/([\w.-]+?)(?:#(.+))?$")
_NPM_GIT = re.compile(r"^git\+(?:https?|ssh)://[^/\s]+/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:#(.+))?$")
_GO_ONE = re.compile(r"^require\s+(\S+)\s+(v\S+)")
_GO_ROW = re.compile(r"^(\S+)\s+(v\S+)")
NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def is_requirements(path: str) -> bool:
    parts = path.lower().split("/")
    base = parts[-1]
    if not base.endswith(".txt"):
        return False
    return base.startswith("requirements") or (len(parts) > 1 and parts[-2] == "requirements")


def _dict(x: object) -> dict:
    return x if isinstance(x, dict) else {}


MAX_DEPS = 1000  # per manifest section: a real one has dozens


def _line_of(text: str, *needles: str) -> int:
    """The 1-based line of the first needle found. ``str.find`` and ``count`` run
    in C, so a thousand deps over a 256 KiB manifest stays well under a second."""
    for needle in needles:
        at = text.find(needle)
        if at >= 0:
            return text.count("\n", 0, at) + 1
    return 1


def _py_entry(sink: Sink, path: str, line: int, entry: str, own: str | None) -> None:
    g = _GIT.search(entry)
    if g:
        sink.add(REPO_DEP, norm_repo(f"{g.group(1)}/{g.group(2)}"), path, line, g.group(3) or "")
        return
    m = _REQ.match(entry.strip())
    if not m or m.group(2).startswith(":"):
        return
    spec = m.group(2).split(";")[0].strip()
    if spec.startswith("@"):
        spec = spec[1:].strip()
    key = norm_package("py", m.group(1))
    if key and key != own:
        sink.add(DEP, key, path, line, spec)


def scan_requirements(path: str, text: str, sink: Sink) -> None:
    for i, ln in enumerate(split_lines(text)):
        s = re.split(r"\s+#", ln.strip(), maxsplit=1)[0].strip()
        if s and not s.startswith(("#", "-")):
            _py_entry(sink, path, i + 1, s, None)


def _pyproject(path: str, text: str, sink: Sink) -> None:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError):
        return
    proj = _dict(data.get("project"))
    poetry = _dict(_dict(data.get("tool")).get("poetry"))
    name = proj.get("name") or poetry.get("name")
    own = norm_package("py", name) if isinstance(name, str) else None

    def entries(seq: object) -> None:
        for e in (seq if isinstance(seq, list) else [])[:MAX_DEPS]:
            if isinstance(e, str):
                _py_entry(sink, path, _line_of(text, e), e, own)

    entries(proj.get("dependencies"))
    for group in _dict(proj.get("optional-dependencies")).values():
        entries(group)
    for group in _dict(data.get("dependency-groups")).values():
        entries(group)
    for dep, val in list(_dict(poetry.get("dependencies")).items())[:MAX_DEPS]:
        if dep.lower() == "python":
            continue
        n = _line_of(text, dep)
        if isinstance(val, dict) and "git" in val:
            m = _GIT.search("git+" + str(val["git"]).removeprefix("git+"))
            ref = val.get("rev") or val.get("tag") or val.get("branch") or ""
            if m:
                sink.add(REPO_DEP, norm_repo(f"{m.group(1)}/{m.group(2)}"), path, n, str(ref))
            continue
        spec = val if isinstance(val, str) else str(_dict(val).get("version", ""))
        key = norm_package("py", dep)
        if key and key != own:
            sink.add(DEP, key, path, n, spec)


def _package_json(path: str, text: str, sink: Sink) -> None:
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return
    data = _dict(data)
    own = data.get("name")
    for section in NPM_SECTIONS:
        for name, val in list(_dict(data.get(section)).items())[:MAX_DEPS]:
            if not isinstance(val, str) or name == own:
                continue
            n = _line_of(text, f'"{name}"')
            g = _NPM_GIT.match(val) or _NPM_GH.match(val)
            if g:
                key = norm_repo(f"{g.group(1)}/{g.group(2)}")
                sink.add(REPO_DEP, key, path, n, g.group(3) or "")
                continue
            sink.add(DEP, norm_package("npm", name), path, n, val)


def _gomod(path: str, text: str, sink: Sink) -> None:
    in_block = False
    for i, raw in enumerate(split_lines(text)):
        ln = raw.split("//", 1)[0].strip()
        if in_block:
            if ln.startswith(")"):
                in_block = False
                continue
            m = _GO_ROW.match(ln)
        elif re.match(r"^require\s*\(", ln):
            in_block = True
            continue
        else:
            m = _GO_ONE.match(ln)
        if m:
            sink.add(DEP, norm_package("go", m.group(1)), path, i + 1, m.group(2))


def scan_deps(path: str, text: str, sink: Sink) -> None:
    base = path.rpartition("/")[2].lower()
    if is_requirements(path):
        scan_requirements(path, text, sink)
    elif len(text) > MAX_FILE_BYTES:
        return
    elif base == "pyproject.toml":
        _pyproject(path, text, sink)
    elif base == "package.json":
        _package_json(path, text, sink)
    elif base == "go.mod":
        _gomod(path, text, sink)
