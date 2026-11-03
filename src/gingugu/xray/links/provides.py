"""X23: what a repo PROVIDES - packages, images, hosts, routes and IaC resources.

``extract({repo path: text})`` reads each file where the code sets a thing up
(a manifest, a CI build, an ingress, a decorator, a Terraform block) and returns
``Fact``s. Pure and bounded: line-by-line anchored scans over capped lines, no
recursion, and a malformed file contributes nothing.
"""

from __future__ import annotations

import json
import re
import tomllib

from gingugu.xray.links import model, provides_hosts, provides_iac, provides_routes
from gingugu.xray.links.model import HOST, IMAGE, PACKAGE, RESOURCE, ROUTE, Fact
from gingugu.xray.links.provides_util import Hit, lines_of, scalar

_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_YAML_EXTS = (".yaml", ".yml")
_SHELL_EXTS = (".sh", ".bash", ".zsh")
_SECTION = re.compile(r"^\s*\[([^\[\]]+)\]\s*$")
_NAME_LINE = re.compile(r"^\s*name\s*[=:]\s*(.*)$")
_GO_MODULE = re.compile(r"^\s*module\s+(\S+)")
_DOCKER_BUILD = re.compile(r"\bdocker\s+(?:buildx\s+)?build\b")
_TAG_FLAG = re.compile(r"(?:^|\s)(?:-t|--tag)(?:\s+|=)(\S+)")
_DOCKER_PUSH = re.compile(r"\bdocker\s+push\s+(?:-\S+\s+)*(\S+)")
_ENV_IMAGE = re.compile(
    r"^\s*(?:ECR_REPOSITORY|IMAGE_NAME|IMAGE|DOCKER_IMAGE|REGISTRY_IMAGE)\s*:\s*(.*)$"
)
_TAGS_KEY = re.compile(r"^(\s*)tags\s*:\s*(.*)$")
_NEW_STEP = re.compile(r"^\s*-\s+(?:name|uses|run)\s*:")


def _section_name_line(lines: list[str], section: str) -> int:
    current = ""
    for n, ln in enumerate(lines, 1):
        h = _SECTION.match(ln)
        if h:
            current = h.group(1).strip()
        elif current == section and _NAME_LINE.match(ln):
            return n
    return 1


def _table(data: object, *keys: str) -> object:
    for k in keys:
        data = data.get(k) if isinstance(data, dict) else None
    return data


def _packages(base: str, text: str, lines: list[str]) -> list[Hit]:
    out: list[Hit] = []
    if base == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except Exception:
            return out
        for section, keys in (("project", ("project", "name")),
                              ("tool.poetry", ("tool", "poetry", "name"))):  # fmt: skip
            name = _table(data, *keys)
            key = model.norm_package("py", name) if isinstance(name, str) else None
            if key:
                out.append((PACKAGE, key, _section_name_line(lines, section)))
    elif base == "setup.cfg":
        current = ""
        for n, ln in enumerate(lines, 1):
            h = _SECTION.match(ln)
            if h:
                current = h.group(1).strip()
            elif current == "metadata" and (m := _NAME_LINE.match(ln)):
                key = model.norm_package("py", scalar(m.group(1)))
                if key:
                    out.append((PACKAGE, key, n))
                break
    elif base == "package.json":
        try:
            data = json.loads(text)
        except Exception:
            return out
        name = data.get("name") if isinstance(data, dict) else None
        key = model.norm_package("npm", name) if isinstance(name, str) else None
        if key:
            pat = re.compile(r'"name"\s*:\s*' + re.escape(json.dumps(name)))
            n = next((i for i, ln in enumerate(lines, 1) if pat.search(ln)), 1)
            out.append((PACKAGE, key, n))
    elif base == "go.mod":
        for n, ln in enumerate(lines, 1):
            m = _GO_MODULE.match(ln)
            if m:
                key = model.norm_package("go", m.group(1).strip("\"'`"))
                if key:
                    out.append((PACKAGE, key, n))
                break
    return out


def _build_push_tags(lines: list[str]) -> list[Hit]:
    out: list[Hit] = []
    active = False
    i = 0
    while i < len(lines):
        ln = lines[i]
        i += 1
        if "build-push-action" in ln:
            active = True
            continue
        if active and _NEW_STEP.match(ln):
            active = False
        m = _TAGS_KEY.match(ln) if active else None
        if not m:
            continue
        val = m.group(2).strip()
        if val and not val.startswith(("|", ">", "#")):
            out.extend((IMAGE, scalar(t), i) for t in val.split(","))
            continue
        ind = len(m.group(1))
        while i < len(lines):
            item = lines[i]
            if item.strip() and len(item) - len(item.lstrip()) <= ind:
                break
            text = item.strip().removeprefix("- ")
            if text:
                out.extend((IMAGE, scalar(t), i + 1) for t in text.split(","))
            i += 1
    return out


def _docker_commands(lines: list[str], ci: bool) -> list[Hit]:
    out: list[Hit] = []
    for n, ln in enumerate(lines, 1):
        if "docker" in ln:
            if _DOCKER_BUILD.search(ln):
                out.extend((IMAGE, m.group(1), n) for m in _TAG_FLAG.finditer(ln))
            p = _DOCKER_PUSH.search(ln)
            if p:
                out.append((IMAGE, p.group(1), n))
        if ci and (e := _ENV_IMAGE.match(ln)):
            out.append((IMAGE, scalar(e.group(1)), n))
    return out


def _hits(path: str, text: str) -> list[Hit]:
    base = path.rpartition("/")[2].lower()
    ext = base[base.rfind(".") :] if "." in base else ""
    lines = lines_of(text)
    ci = model.is_ci(path)
    out = _packages(base, text, lines)
    if base == "cname":
        out += provides_hosts.cname_file(lines)
    if ci or base.startswith(("makefile", "jenkinsfile")) or ext in _SHELL_EXTS:
        out += _docker_commands(lines, ci)
    if ci:
        out += _build_push_tags(lines)
    if ext == ".tf":
        out += provides_iac.terraform(lines)
    if ext in _YAML_EXTS:
        out += provides_hosts.yaml_hosts(lines)
    if ext in _YAML_EXTS or ext == ".json":
        is_json = ext == ".json"
        out += provides_routes.openapi_routes(text, lines, is_json)
        out += provides_hosts.openapi_servers(text, lines, is_json)
        out += provides_iac.cloudformation(lines)
    if ext == ".py":
        out += provides_routes.python_routes(lines)
    elif ext in _JS_EXTS:
        out += provides_routes.js_routes(lines) + provides_routes.next_routes(path)
    elif ext == ".go":
        out += provides_routes.go_routes(lines)
    if ext in model.CODE_EXTS and ext not in _SHELL_EXTS:
        out += provides_iac.cdk(text, lines)
    if ext not in (".json", ".lock") and "server_name" in text:
        out += provides_hosts.nginx(lines)
    return out


def _normalize(kind: str, raw: str) -> str | None:
    if kind in (ROUTE, provides_routes.TAIL):
        return model.norm_route(raw)
    if kind == HOST:
        return model.norm_host(raw)
    if kind == IMAGE:
        return model.norm_image(raw)
    if kind == RESOURCE:
        return raw if 0 < len(raw) <= model.MAX_KEY_CHARS else None
    return raw if kind == PACKAGE else None


def extract(files: dict[str, str]) -> list[Fact]:
    """The package, image, host, route and resource facts the files provide. A
    test or fixture provides nothing: what it serves is a stand-in."""
    best: dict[tuple[str, str, str], tuple[int, str]] = {}
    for path, text in files.items():
        if not isinstance(text, str) or not model.wanted(path) or model.is_doc(path):
            continue
        if model.is_test(path) or len(text) > model.MAX_FILE_BYTES:
            continue
        try:
            hits = _hits(path, text)
        except Exception:  # a malformed file contributes nothing
            continue
        for kind, raw, line in hits:
            key = _normalize(kind, raw)
            if not key:
                continue
            detail = model.UNMOUNTED if kind == provides_routes.TAIL else ""
            ident = (ROUTE if detail else kind, key, path)
            prev = best.get(ident)
            # a whole path beats a tail; then the first line
            if prev is None or (detail, line) < (prev[1], prev[0]):
                best[ident] = (line, detail)
    facts = [Fact(k, key, p, line, detail) for (k, key, p), (line, detail) in best.items()]
    return sorted(facts, key=lambda f: (f.path, f.line, f.kind, f.key))
