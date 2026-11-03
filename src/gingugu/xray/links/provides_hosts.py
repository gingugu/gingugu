"""X23: hostnames a repo serves - ingress, gateway, DNS, nginx, OpenAPI servers."""

from __future__ import annotations

import re

from gingugu.xray.links.model import HOST
from gingugu.xray.links.provides_util import (
    Hit,
    first_line_with,
    is_yaml_openapi,
    json_openapi,
    scalar,
    url_host,
)

_PROVIDER_KINDS = frozenset(
    {"Ingress", "HTTPRoute", "GRPCRoute", "Gateway", "VirtualService", "IngressRoute",
     "Certificate"}
)  # fmt: skip
_KIND = re.compile(r"^kind:\s*[\"']?(\w+)")
_HKEY = re.compile(r"^(\s*)(-\s+)?(?:hosts?|hostnames|dnsNames|commonName)\s*:\s*(.*)$")
_ITEM = re.compile(r"^(\s*)-\s+(.*)$")
_INGRESS = re.compile(r"^(\s*)ingress\s*:\s*$")
_NGINX = re.compile(r"^\s*server_name\s+([^;]+);")
_META_KEY = re.compile(r"^\s*(name|namespace)\s*:\s*(.*)$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _blank(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def _hosts_in(lines: list[str], start: int, end: int) -> list[Hit]:
    out: list[Hit] = []
    for j in range(start, end):
        m = _HKEY.match(lines[j])
        if not m:
            continue
        keycol = len(m.group(1)) + len(m.group(2) or "")
        val = m.group(3).strip()
        if val.startswith("["):
            out.extend((HOST, scalar(p), j + 1) for p in val.strip("[]").split(","))
        elif not val or val.startswith("#"):
            for k in range(j + 1, end):
                if _blank(lines[k]):
                    continue
                it = _ITEM.match(lines[k])
                if not it or len(it.group(1)) < keycol:
                    break
                out.append((HOST, scalar(it.group(2)), k + 1))
        else:
            out.append((HOST, scalar(val), j + 1))
    return out


def _helm_ingress(lines: list[str], start: int, end: int) -> list[Hit]:
    out: list[Hit] = []
    for j in range(start, end):
        m = _INGRESS.match(lines[j])
        if not m:
            continue
        ind = len(m.group(1))
        stop = end
        for k in range(j + 1, end):
            if not _blank(lines[k]) and _indent(lines[k]) <= ind:
                stop = k
                break
        out.extend(_hosts_in(lines, j + 1, stop))
    return out


def _service(lines: list[str], start: int, end: int) -> list[Hit]:
    for j in range(start, end):
        if lines[j].rstrip() != "metadata:":
            continue
        vals: dict[str, tuple[str, int]] = {}
        ind = None
        for k in range(j + 1, end):
            if _blank(lines[k]):
                continue
            cur = _indent(lines[k])
            if cur == 0:
                break
            ind = cur if ind is None else ind
            m = _META_KEY.match(lines[k]) if cur == ind else None
            if m:
                vals[m.group(1)] = (scalar(m.group(2)), k + 1)
        if "name" in vals and "namespace" in vals:
            (name, line), (ns, _) = vals["name"], vals["namespace"]
            return [
                (HOST, f"{name}.{ns}.svc.cluster.local", line),
                (HOST, f"{name}.{ns}.svc", line),
            ]
        return []
    return []


def yaml_hosts(lines: list[str]) -> list[Hit]:
    bounds = [i for i, ln in enumerate(lines) if ln.startswith("---")]
    edges = [-1, *bounds, len(lines)]
    out: list[Hit] = []
    for a, b in zip(edges, edges[1:], strict=False):
        start = a + 1
        kind = next((m.group(1) for ln in lines[start:b] if (m := _KIND.match(ln))), "")
        if kind in _PROVIDER_KINDS:
            out.extend(_hosts_in(lines, start, b))
        else:
            out.extend(_helm_ingress(lines, start, b))
        if kind == "Service":
            out.extend(_service(lines, start, b))
    return out


def nginx(lines: list[str]) -> list[Hit]:
    out: list[Hit] = []
    for n, ln in enumerate(lines, 1):
        if "server_name" not in ln:
            continue
        m = _NGINX.match(ln)
        if m:
            out.extend((HOST, h, n) for h in m.group(1).split() if not h.startswith("~"))
    return out


def openapi_servers(text: str, lines: list[str], is_json: bool) -> list[Hit]:
    out: list[Hit] = []
    if is_json:
        data = json_openapi(text)
        servers = data.get("servers") if data else None
        for s in (servers if isinstance(servers, list) else [])[:50]:
            url = s.get("url") if isinstance(s, dict) else None
            if isinstance(url, str):
                out.append((HOST, url_host(url), first_line_with(lines, url)))
        return out
    if not is_yaml_openapi(lines):
        return out
    inside = False
    for n, ln in enumerate(lines, 1):
        if not inside:
            inside = ln.rstrip() == "servers:"
            continue
        if re.match(r"^[A-Za-z]", ln):
            inside = False
            continue
        m = re.match(r"^\s*-?\s*url\s*:\s*(.*)$", ln)
        if m:
            out.append((HOST, url_host(scalar(m.group(1))), n))
    return out


def cname_file(lines: list[str]) -> list[Hit]:
    return [
        (HOST, ln.split()[0], n)
        for n, ln in enumerate(lines, 1)
        if ln.strip() and not ln.strip().startswith("#")
    ]
