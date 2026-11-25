"""Terraform parse: one linear pass over the bytes of a ``.tf`` file, no dependency.

Comments are stripped outside strings; braces are counted outside strings and
heredocs; a heredoc body is opaque to the structure but still scanned for
references. The result depends on the bytes alone and never raises.
"""

from __future__ import annotations

import re

MAX_NAME = 256
MAX_SOURCE = 1024
MAX_ITEMS = 200_000

# One token at a time; anything else is a run of plain text.
_TOKEN = re.compile(
    r"(?P<com>\#[^\n]*|//[^\n]*|/\*(?:.*?\*/|.*))"
    r'|(?P<str>"(?:[^"\\\n]|\\.)*"?)'
    r"|(?P<here><<-?(?P<mark>[A-Za-z_]\w*)[ \t]*\r?\n)"
    r'|(?P<text>[^"#/<]+|.)',
    re.DOTALL,
)
_STRUCT = re.compile(
    r"(?P<b>[{}])"
    r'|(?P<hdr>^[ \t]*(?:(?P<k2>resource|data)[ \t]+"(?P<t>[^"\n]*)"[ \t]+"(?P<n2>[^"\n]*)"'
    r'|(?P<k1>variable|module|output)[ \t]+"(?P<n1>[^"\n]*)"))'
    r"|(?P<loc>^[ \t]*locals[ \t]*(?=\{))"
    r'|(?P<kv>^[ \t]*(?P<key>[A-Za-z_][\w-]*|"[^"\n]*")[ \t]*[=:](?!=))',
    re.MULTILINE,
)
_SOURCE = re.compile(r'[ \t]*"([^"\n]*)"')
_REF = re.compile(
    r"(?<![\w.])(?:"
    r"(?P<k>var|local|module)\.(?P<n>[A-Za-z_][\w-]*)"
    r"|data\.(?P<dt>[a-z][a-z0-9]*_[a-z0-9_]+)\.(?P<dn>[A-Za-z_][\w-]*)"
    r"|(?P<rt>[a-z][a-z0-9]*_[a-z0-9_]+)\.(?P<rn>[A-Za-z_][\w-]*))"
)


def _clean(text: str) -> tuple[str, str]:
    """(code, skeleton): ``code`` is the text without comments; ``skeleton`` also
    blanks heredoc bodies and the braces inside strings, so it holds structure only."""
    code: list[str] = []
    skel: list[str] = []
    pos, end = 0, len(text)
    while pos < end:
        m = _TOKEN.match(text, pos)
        if m is None:  # unreachable: the last alternative matches any char
            break
        pos = m.end()
        if m["com"] is not None:
            code.append(" ")
            skel.append(" ")
        elif m["str"] is not None:
            code.append(m["str"])
            skel.append(m["str"].replace("{", "_").replace("}", "_"))
        elif m["here"] is not None:
            stop = re.compile(rf"^[ \t]*{re.escape(m['mark'])}[ \t]*\r?$", re.MULTILINE)
            hit = stop.search(text, pos)
            body_end = hit.start() if hit else end
            code.append(m["here"] + text[pos:body_end])
            skel.append(m["here"])
            pos = hit.end() if hit else end
        else:
            code.append(m["text"])
            skel.append(m["text"])
    return "".join(code), "".join(skel)


def _declarations(skel: str) -> tuple[list[str], list[list[str]]]:
    decl: list[str] = []
    mods: list[list[str]] = []
    depth = 0
    pending: tuple[str, str] | None = None  # (kind, name) awaiting its "{"
    cur: tuple[str, str] | None = None
    for m in _STRUCT.finditer(skel):
        if m["b"] == "{":
            if depth == 0:
                cur, pending = pending, None
            depth += 1
        elif m["b"] == "}":
            depth = max(depth - 1, 0)
            if depth == 0:
                cur = None
        elif depth > 0:
            if m["kv"] and depth == 1 and cur:
                key = m["key"].strip('"')
                if cur[0] == "locals" and len(key) <= MAX_NAME:
                    decl.append(f"local.{key}")
                elif cur[0] == "module" and key == "source":
                    s = _SOURCE.match(skel, m.end())
                    if s:
                        mods.append([cur[1], s[1][:MAX_SOURCE]])
        elif m["hdr"]:
            if m["k2"]:
                name = f"{m['t']}.{m['n2']}"
                decl.append(name if m["k2"] == "resource" else f"data.{name}")
                pending = (m["k2"], name)
            else:
                prefix = {"variable": "var.", "module": "module.", "output": ""}[m["k1"]]
                if prefix:
                    decl.append(prefix + m["n1"])
                pending = (m["k1"], m["n1"])
        elif m["loc"]:
            pending = ("locals", "")
    return [d for d in decl if len(d) <= MAX_NAME], mods[:MAX_ITEMS]


def _references(code: str) -> list[str]:
    refs: set[str] = set()
    for m in _REF.finditer(code):
        if m["k"]:
            refs.add(f"{m['k']}.{m['n']}")
        elif m["dt"]:
            refs.add(f"data.{m['dt']}.{m['dn']}")
        else:
            refs.add(f"{m['rt']}.{m['rn']}")
        if len(refs) >= MAX_ITEMS:
            break
    return sorted(r for r in refs if len(r) <= MAX_NAME)


def parse(data: bytes) -> dict:
    """``{"decl": [...], "refs": [...], "mods": [[name, source]]}`` for one file."""
    try:
        code, skel = _clean(data.decode("utf-8", errors="replace"))
        decl, mods = _declarations(skel)
        return {"decl": sorted(set(decl)), "refs": _references(code), "mods": mods}
    except Exception:  # noqa: BLE001 - a hostile file must never take the scan down
        return {"decl": [], "refs": [], "mods": []}


def _strings(value: object, limit: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= MAX_ITEMS
        and all(isinstance(s, str) and len(s) <= limit for s in value)
    )


def valid(raw: object) -> bool:
    """Whether ``raw`` has exactly the shape ``parse`` returns."""
    return (
        isinstance(raw, dict)
        and set(raw) == {"decl", "refs", "mods"}
        and _strings(raw["decl"], MAX_NAME)
        and _strings(raw["refs"], MAX_NAME)
        and isinstance(raw["mods"], list)
        and len(raw["mods"]) <= MAX_ITEMS
        and all(
            isinstance(m, list)
            and len(m) == 2
            and isinstance(m[0], str)
            and len(m[0]) <= MAX_NAME
            and isinstance(m[1], str)
            and len(m[1]) <= MAX_SOURCE
            for m in raw["mods"]
        )
    )
