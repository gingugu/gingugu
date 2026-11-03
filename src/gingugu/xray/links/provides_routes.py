"""X23: routes a repo serves - decorators, route tables, file routes, OpenAPI.

A route on the application itself (``app = FastAPI()``, express ``app``, a Go
``HandleFunc``, a file route, an OpenAPI path) is its whole path. A route on a
router mounted somewhere else (``APIRouter``, ``Blueprint``, express
``Router()``, a Go group, Django ``include``) is only its tail: those are
reported as ``TAIL`` and become ``ROUTE`` facts marked ``model.UNMOUNTED``.
"""

from __future__ import annotations

import re

from gingugu.xray.links.model import ROUTE
from gingugu.xray.links.provides_util import (
    Hit,
    first_line_with,
    is_yaml_openapi,
    json_openapi,
)

TAIL = "route-tail"  # internal: a route whose mount prefix lives in another file

_VERBS = r"get|post|put|patch|delete|head|options|route|api_route|websocket"
_PY_DEC = re.compile(rf'^\s*@(\w+(?:\.\w+)*)\.(?:{_VERBS})\(\s*(["\'])([^"\']*)\2')
_PY_PREFIX_DEF = re.compile(r"^\s*(\w+)\s*(?::[^=\n]*)?=\s*(?:[\w.]+\.)?(?:APIRouter|Blueprint)\(")
_PY_PREFIX = re.compile(r'(?<!\w)(?:url_)?prefix\s*=\s*(["\'])([^"\']*)\1')
_PY_APP_DEF = re.compile(
    r"^\s*(\w+)\s*(?::[^=\n]*)?=\s*(?:[\w.]+\.)?(?:FastAPI|Flask|Starlette|Quart|Sanic|APIFlask)\("
)
_PY_TABLES = (
    re.compile(r'\bRoute\(\s*(["\'])(/[^"\']*)\1'),
    re.compile(r'\badd_url_rule\(\s*(["\'])(/[^"\']*)\1'),
    re.compile(r'\bweb\.(?:get|post|put|patch|delete|head|view)\(\s*(["\'])(/[^"\']*)\1'),
)
_DJANGO = re.compile(r'(?<![\w.])path\(\s*(["\'])([^"\']*)\1')
_JS = re.compile(
    r"\b([A-Za-z_$][\w$]*)\.(?:get|post|put|patch|delete|all|head|options|route)\("
    r"\s*([\"'`])(/[^\"'`]*)\2"
)
_JS_RECV = frozenset({"app", "router", "server", "fastify", "hono"})
_GO_HANDLE = re.compile(r'\.(?:HandleFunc|Handle)\(\s*"([^"]*)"')
_GO_VERB = re.compile(
    r'\.(?:GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete|Any)\(\s*"(/[^"]*)"'
)
_GO_METHOD = re.compile(r"^[A-Z]+\s+")
# Anywhere in the tree, so a monorepo's web/app/... is found too; first app/ wins.
_NEXT_APP = re.compile(r"^(?:[\w.@-]+/)*?(?:src/)?app/(?:(.*)/)?route\.(?:ts|js|tsx|jsx)$")
_NEXT_PAGES = re.compile(r"^(?:[\w.@-]+/)*?(?:src/)?pages/(api/.+)\.(?:ts|js|tsx|jsx)$")
_YAML_PATH = re.compile(r'^(\s+)(["\']?)(/[^\s"\']*)\2\s*:')


def _join(prefix: str, path: str) -> str:
    if not path:
        return prefix
    if not path.startswith("/"):
        return ""
    return prefix.rstrip("/") + path if prefix else path


def python_routes(lines: list[str]) -> list[Hit]:
    prefixes: dict[str, str] = {}
    apps: set[str] = set()
    for ln in lines:
        d = _PY_PREFIX_DEF.match(ln)
        if d:
            p = _PY_PREFIX.search(ln)
            if p:
                prefixes[d.group(1)] = p.group(2)
        a = _PY_APP_DEF.match(ln)
        if a:
            apps.add(a.group(1))
    out: list[Hit] = []
    for n, ln in enumerate(lines, 1):
        m = _PY_DEC.match(ln)
        if m:
            recv = m.group(1).rpartition(".")[2]
            full = _join(prefixes.get(recv, ""), m.group(3))
            if full:
                out.append((ROUTE if recv in apps else TAIL, full, n))
        for rx in _PY_TABLES:
            for t in rx.finditer(ln):
                out.append((ROUTE, t.group(2), n))
        for t in _DJANGO.finditer(ln):
            out.append((TAIL, "/" + t.group(2).lstrip("/"), n))  # include() adds the prefix
    return out


def _js_receiver(name: str) -> bool:
    return name in _JS_RECV or name.endswith(("Router", "router", "App", "app"))


def js_routes(lines: list[str]) -> list[Hit]:
    out: list[Hit] = []
    for n, ln in enumerate(lines, 1):
        for m in _JS.finditer(ln):
            recv = m.group(1)
            if _js_receiver(recv):
                mounted = not recv.lower().endswith("router")  # app.use("/x", router)
                out.append((ROUTE if mounted else TAIL, m.group(3), n))
    return out


def next_routes(path: str) -> list[Hit]:
    m = _NEXT_APP.match(path)
    if m:
        rel = m.group(1) or ""
    else:
        m = _NEXT_PAGES.match(path)
        if not m:
            return []
        rel = m.group(1)
    segs = [s for s in rel.split("/") if s and not (s.startswith("(") and s.endswith(")"))]
    if segs and segs[-1] == "index":
        segs.pop()
    return [(ROUTE, "/" + "/".join(segs), 1)]


def go_routes(lines: list[str]) -> list[Hit]:
    out: list[Hit] = []
    grouped = any(".Group(" in ln or ".Route(" in ln for ln in lines)  # gin/echo/chi prefixes
    for n, ln in enumerate(lines, 1):
        for m in _GO_HANDLE.finditer(ln):
            out.append((ROUTE, _GO_METHOD.sub("", m.group(1)), n))
        for m in _GO_VERB.finditer(ln):
            out.append((TAIL if grouped else ROUTE, m.group(1), n))
    return out


def openapi_routes(text: str, lines: list[str], is_json: bool) -> list[Hit]:
    if is_json:
        data = json_openapi(text)
        paths = data.get("paths") if data else None
        if not isinstance(paths, dict):
            return []
        wanted = {k for k in paths if isinstance(k, str)}
        found: dict[str, int] = {}
        for n, ln in enumerate(lines, 1):
            for key in re.findall(r'"(/[^"]*)"', ln):
                if key in wanted:
                    found.setdefault(key, n)
        return [(ROUTE, k, found.get(k) or first_line_with(lines, k)) for k in wanted]
    if not is_yaml_openapi(lines):
        return []
    out: list[Hit] = []
    in_paths, indent = False, None
    for n, ln in enumerate(lines, 1):
        if not in_paths:
            in_paths = ln.rstrip() == "paths:"
            continue
        if ln.strip() and not ln.startswith((" ", "\t", "#")):
            in_paths = False
            continue
        m = _YAML_PATH.match(ln)
        if m and indent in (None, len(m.group(1))):
            indent = len(m.group(1))
            out.append((ROUTE, m.group(3), n))
    return out
