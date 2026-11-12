"""X23 mentions: hosts, routes, resource-shaped names and sha citations."""

from __future__ import annotations

import functools
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
    """Quoted string literals (", ', `) on one line, each at most 500 chars. A
    doubled backtick (an RST or markdown code span in a comment) counts as one."""
    line = line.replace("``", "`")
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
        if ch in "})`" and lit.startswith("/", k + 1):  # `{base}/x`, `(url)/x`, a `/code` span
            rest = lit[k + 1 :]
            if ch == "`":  # a code span inside prose: the path ends where the span does
                rest = rest.split("`", 1)[0]
            return norm_route(rest)
    return None


# A NAME is kept in the links cache on disk, so a value that may be a credential is
# never one: not on a line that sets something secret-named, and not when any part
# of it looks random (a key, a token, a hash). Resource names are words.
# A line that SETS something secret-named (``API_KEY=``, ``client_secret:``), not one
# that merely says the word: a repo called "auth-service" is still named.
_SECRETISH = re.compile(
    r"(?:secret|token|passw|pwd|api[_-]?key|apikey|private[_-]?key|credential|auth|bearer"
    r"|session|cookie|signature|salt|dsn|license)[\w.-]*[\"']?\s*[:=]",
    re.IGNORECASE,
)
_PART = re.compile(r"[-_.]")
# The same words as plain substrings: a C-speed `in` test turns most lines away
# before the regex runs (measured: the guard was 14M regex searches on a cold read).
_SECRET_WORDS = (
    "secret", "token", "passw", "pwd", "key", "credential", "auth", "bearer", "session",
    "cookie", "signature", "salt", "dsn", "license",
)  # fmt: skip


@functools.lru_cache(maxsize=8192)
def secretish(line: str) -> bool:
    """Whether ``line`` sets something secret-named; shared by every scanner of a line."""
    if "=" not in line and ":" not in line:
        return False
    low = line.lower()
    return any(w in low for w in _SECRET_WORDS) and bool(_SECRETISH.search(line))


_TOKEN_PREFIX = re.compile(
    r"^(?:gh[pousr]_|github_pat_|sk-|sk_|pk_|rk_|xox[abpors]-|AKIA|ASIA|AIza|ya29\.|eyJ|glpat-|"
    r"npm_|pypi-|hf_|shpat_|SG\.)"
)


def _random_part(value: str) -> bool:
    if _TOKEN_PREFIX.match(value):
        return True  # a well-known credential prefix, however short the rest
    if len(value) < 12:
        return False  # too short for either random-looking shape below
    for part in _PART.split(value):
        has_digit = any(c.isdigit() for c in part)
        if len(part) >= 16 and has_digit and any(c.isalpha() for c in part):
            return True
        if len(part) >= 12 and has_digit and all(c in "0123456789abcdefABCDEF" for c in part):
            return True
    return False


def _name_shaped(value: str) -> bool:
    if not _NAME.fullmatch(value) or _random_part(value):
        return False
    return len(value) >= 6 or any(c in value for c in "-_.")


def _add_name(sink: Sink, value: str, path: str, line: int) -> None:
    if sink.names < MAX_NAMES and _name_shaped(value):
        if sink.add(NAME, value, path, line):
            sink.names += 1


def scan_net(path: str, lines: list[str], sink: Sink, code: bool) -> None:
    # an env file is where secrets live: its hosts count, its values are never names
    env_file = path.rpartition("/")[2].lower().startswith(".env")
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        n = i + 1
        if "://" in ln:
            for m in _URL.finditer(ln):
                host = norm_host(m.group(1))
                sink.add(HOST, host, path, n)
                rest = m.group(2)
                if rest and not re.split(r"[?#]", rest, maxsplit=1)[0].endswith(".git"):
                    route = norm_route(rest)
                    if route and route != "/":
                        # the host goes with it (X7b): a path on another host is not ours
                        sink.add(ROUTE, route, path, n, detail=host or "", local_first=True)
        kv = _KV_HOST.match(ln) if "host" in ln.lower() else None
        if kv and "host" in kv.group(1).lower() and (kv.group(2) or not code):
            sink.add(HOST, norm_host(kv.group(3)), path, n)
        quiet = env_file or secretish(ln)
        if '"' in ln or "'" in ln or "`" in ln:
            for lit in literals(ln):
                route = _route_in(lit)
                if route and route != "/":
                    sink.add(ROUTE, route, path, n, local_first=True)
                if not quiet:
                    _add_name(sink, lit, path, n)
        if not code and not quiet and (":" in ln or "=" in ln):
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
