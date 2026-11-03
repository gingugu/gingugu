"""X23: the shapes of cross-repo links - what a repo provides, what it mentions.

A service ecosystem is wired by names, not imports: a hostname one repo serves
and another calls, a route one defines and another requests, a table one
creates and another reads, a package one publishes and another pins, a repo one
CI job checks out. Each repo is read for two sets of such names: the ones it
*provides* (found where the code sets the thing up) and the ones it *mentions*.
A mention in B of a name A provides is a link from B to A. Nothing is declared
by hand: every fact is read out of code, config, IaC, CI and manifests.

Pure: text in, facts out. No git, no disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- provided kinds (a repo sets the thing up) -------------------------------
PACKAGE = "package"  # key: "py:<pep503>", "npm:<name>", "go:<module path>"
IMAGE = "image"  # key: image path without registry, tag or digest
HOST = "host"  # key: lowercased hostname; "*.x.com" for a wildcard
ROUTE = "route"  # key: normalized path, see ``norm_route``; detail UNMOUNTED when partial
UNMOUNTED = "unmounted"  # a route on a router mounted elsewhere: the key is only its tail
RESOURCE = "resource"  # key: an IaC-owned name (table, bucket, queue...); "*" globs
PROVIDED = (PACKAGE, IMAGE, HOST, ROUTE, RESOURCE)

# --- mentioned kinds (a repo leans on something) -----------------------------
CI = "ci"  # key: "owner/name" a CI job checks out, reuses or acts on; detail: ref
SUBMODULE = "submodule"  # key: "owner/name"
REPO_DEP = "repo-dep"  # key: "owner/name" a manifest pins by git URL; detail: ref
DEP = "dep"  # key: as PACKAGE; detail: the version spec
NAME = "name"  # key: a resource-shaped literal, matched against RESOURCE
CITE = "cite"  # key: "owner/name" or "name"; detail: sha; target: cited path ("" if none)
DOC = "doc"  # key: "owner/name" or "name"; target: path in that repo
REPO_NAME = "repo-name"  # key: a repo-shaped name or "owner/name" anywhere; detail: link kind
# IMAGE, HOST and ROUTE are mentioned under the same names they are provided.
MENTIONED = (CI, SUBMODULE, REPO_DEP, DEP, IMAGE, HOST, ROUTE, NAME, CITE, DOC, REPO_NAME)

# --- link kinds (what the edge means, for the agent) -------------------------
LINK_KINDS = ("ci", "source", "pin", "deploy", "runtime", "data", "cite", "docs", "mention")

DOC_EXTS = (".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc")
CODE_EXTS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rb", ".java", ".kt",
    ".cs", ".rs", ".php", ".sh", ".bash", ".zsh", ".ps1",
)  # fmt: skip
CONFIG_EXTS = (
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".tf", ".tfvars", ".hcl", ".ini",
    ".cfg", ".conf", ".env", ".properties", ".xml", ".gradle", ".mod", ".tpl",
)  # fmt: skip
CONFIG_NAMES = (
    "dockerfile", "makefile", "jenkinsfile", "procfile", "cname", ".gitmodules",
    "requirements.txt", ".env", "go.mod", "nginx.conf",
)  # fmt: skip
SKIP_DIRS = frozenset(
    {"node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__", ".git",
     "site-packages", ".next", ".terraform", "coverage", "target", ".tox", "bower_components"}
)  # fmt: skip
LOCKFILES = frozenset(
    {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
     "go.sum", "pipfile.lock", "cargo.lock", "composer.lock", "gemfile.lock"}
)  # fmt: skip
MAX_FILE_BYTES = 262_144
MAX_KEY_CHARS = 300


@dataclass(frozen=True)
class Fact:
    kind: str
    key: str
    path: str  # the file in this repo the fact was read from
    line: int  # 1-based
    detail: str = ""
    target: str = ""


@dataclass(frozen=True)
class Link:
    src: str  # the repo that leans (dependent), by identity
    src_path: str
    src_line: int
    dst: str  # the repo leaned on (dependency)
    dst_path: str | None  # None: the repo as a whole
    kind: str  # one of LINK_KINDS
    key: str  # the name that joined them
    detail: str = ""
    certain: bool = True
    flags: tuple[str, ...] = field(default=())


def _base(path: str) -> str:
    return path.rpartition("/")[2].lower()


def is_doc(path: str) -> bool:
    base = _base(path)
    return base.endswith(DOC_EXTS) and not base.startswith(("requirements", "constraints"))


_TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs", "testdata", "fixtures"})
_TEST_SUFFIXES = (
    "_test.py", "_test.go", ".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts",
    ".spec.tsx", ".spec.js", ".spec.jsx", "_spec.rb", "_test.rb",
)  # fmt: skip


def is_test(path: str) -> bool:
    """A test or fixture: what it serves is a stand-in, so it provides nothing."""
    parts = path.lower().split("/")
    base = parts[-1]
    return (
        any(p in _TEST_DIRS for p in parts[:-1])
        or base.startswith("test_")
        or base == "conftest.py"
        or base.endswith(_TEST_SUFFIXES)
    )


def wanted(path: str) -> bool:
    """Whether a repo file is read for links at all: code, config, CI and docs,
    never a lockfile or anything under a vendored or generated directory."""
    parts = path.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    base = parts[-1].lower()
    if base in LOCKFILES:
        return False
    if base in CONFIG_NAMES or base.startswith(("dockerfile", "jenkinsfile", ".env")):
        return True
    return base.endswith(DOC_EXTS + CODE_EXTS + CONFIG_EXTS)


def is_ci(path: str) -> bool:
    """A CI definition: GitHub workflows and actions, GitLab, Jenkins, CircleCI,
    Buildkite, Azure Pipelines, Bitbucket."""
    low = path.lower()
    base = _base(low)
    return (
        low.startswith((".github/workflows/", ".github/actions/", ".gitlab/", ".buildkite/"))
        or low.startswith(".circleci/")
        or base in (".gitlab-ci.yml", "bitbucket-pipelines.yml", "action.yml", "action.yaml")
        or base.startswith(("jenkinsfile", "azure-pipelines"))
    )


# --- routes --------------------------------------------------------------------

_PARAM = re.compile(
    r"^(?:\{[^{}]*\}|<[^<>]*>|:[A-Za-z_]\w*\??|\$\{[^{}]*\}|\$[A-Za-z_]\w*|\*\w*|\[[^\[\]]*\])$"
)
_SEGMENT = re.compile(r"^[A-Za-z0-9_.~-]+$")
WILD = "*"
GENERIC_ROUTES = frozenset(
    {"/", "/health", "/healthz", "/healthcheck", "/ready", "/readyz", "/live", "/livez",
     "/status", "/metrics", "/ping", "/version", "/docs", "/redoc", "/openapi.json",
     "/swagger.json", "/favicon.ico", "/login", "/logout", "/callback", "/api", "/v1",
     "/v2", "/index", "/static", "/assets", "/robots.txt", "/*",
     "/api/health", "/api/healthz", "/api/status", "/api/version", "/api/ping", "/api/info",
     "/api/me", "/api/login", "/api/logout", "/api/auth", "/api/config", "/api/settings",
     "/api/graphql", "/v1/health", "/v1/status", "/auth/login", "/auth/logout",
     "/auth/callback", "/oauth/callback", "/oauth/token", "/oauth2/callback"}
)  # fmt: skip
# Paths every OpenAI-compatible client and server shares: a link only when the
# same mentioning file also names a host the provider serves.
STANDARD_ROUTES = frozenset(
    {"/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions",
     "/v1/embeddings", "/embeddings", "/v1/models", "/models", "/v1/messages",
     "/v1/responses", "/responses", "/v1/images/generations", "/v1/audio/transcriptions"}
)  # fmt: skip
# Case-sensitive on purpose: "/Users/me" is a Mac home, "/users/{id}" is an API.
FS_PREFIXES = (
    "/etc/", "/usr/", "/var/", "/tmp/", "/opt/", "/home/", "/Users/", "/dev/", "/proc/",
    "/bin/", "/sbin/", "/lib/", "/mnt/", "/srv/", "/root/", "/private/", "/sys/",
    "/Library/", "/Applications/", "/Volumes/", "/nix/", "/github/workspace",
)  # fmt: skip


def norm_route(raw: str) -> str | None:
    """A route as one key: no query or fragment, no trailing or doubled slash,
    every parameter form (``{id}``, ``<int:id>``, ``:id``, ``${id}``, ``[id]``,
    ``*``) as ``*``. None when it is not a URL path: no leading slash, a segment
    that is neither a name nor a parameter, or a filesystem location."""
    if not isinstance(raw, str) or not raw.startswith("/") or len(raw) > MAX_KEY_CHARS:
        return None
    path = re.split(r"[?#]", raw, maxsplit=1)[0]
    if path.startswith(FS_PREFIXES):
        return None
    segs = []
    for seg in path.split("/"):
        if not seg:
            continue
        if _PARAM.match(seg):
            segs.append(WILD)
        elif _SEGMENT.match(seg):
            segs.append(seg)
        else:
            return None
    return "/" + "/".join(segs)


def concrete(route: str) -> int:
    """How many segments of a normalized route are names, not parameters."""
    return sum(1 for s in route.split("/") if s and s != WILD)


# --- other keys ------------------------------------------------------------------

_IP = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?"
_HOST = re.compile(rf"^(?:\*\.)?{_LABEL}(?:\.{_LABEL})+$")
_RESERVED = ("example.com", "example.org", "example.net", "localhost")
_RESERVED_TLDS = (".example", ".test", ".invalid", ".localhost")


def norm_host(raw: str) -> str | None:
    """A hostname as one key (lowercased, no port, no trailing dot), or None for
    an IP, localhost, a reserved example domain, or anything not a hostname."""
    if not isinstance(raw, str):
        return None
    host = raw.strip().lower().rstrip(".")
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    if len(host) > 253 or _IP.match(host) or not _HOST.match(host):
        return None
    bare = host.removeprefix("*.")
    if bare in _RESERVED or bare.endswith(tuple("." + r for r in _RESERVED)):
        return None
    if bare.endswith(_RESERVED_TLDS):
        return None
    return host


_TEMPLATE = ("${", "{{", "$(", "%{", "<%")


def norm_image(raw: str) -> str | None:
    """An image as one key: the repository path without registry host, tag or
    digest, lowercased. None when any part is a template or nothing is left."""
    if not isinstance(raw, str):
        return None
    ref = raw.strip().strip("'\"").split("@", 1)[0]
    parts = ref.split("/")
    registry = parts[0] == "localhost" or "." in parts[0] or ":" in parts[0]
    if len(parts) > 1 and registry:
        parts = parts[1:]
    if parts:
        parts[-1] = parts[-1].split(":", 1)[0]
    if not parts or any(not p or any(t in p for t in _TEMPLATE) for p in parts):
        return None
    key = "/".join(parts).lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", key) or len(key) > MAX_KEY_CHARS:
        return None
    return key


def norm_package(ecosystem: str, name: str) -> str | None:
    """``py:``/``npm:``/``go:`` plus the name as each registry compares it."""
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_KEY_CHARS:
        return None
    name = name.strip()
    if ecosystem == "py":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            return None
        return "py:" + re.sub(r"[-_.]+", "-", name).lower()
    if ecosystem == "npm":
        if not re.fullmatch(r"(?:@[a-z0-9._~-]+/)?[a-z0-9._~-]+", name.lower()):
            return None
        return "npm:" + name.lower()
    if ecosystem == "go":
        if not re.fullmatch(r"[A-Za-z0-9._~/-]+", name):
            return None
        return "go:" + name
    return None


def norm_repo(raw: str) -> str | None:
    """``owner/name`` or ``name``, lowercased, without ``.git``; None otherwise."""
    if not isinstance(raw, str):
        return None
    name = raw.strip().lower().removesuffix(".git")
    parts = name.split("/")
    if not 1 <= len(parts) <= 2:
        return None
    if not all(re.fullmatch(r"[a-z0-9_.-]{1,100}", p) and p[0] not in ".-" for p in parts):
        return None
    return name


_SHA = re.compile(r"^[0-9a-f]{40}$")
_TAG = re.compile(r"^v?\d+(?:\.\d+)*(?:[-+][\w.]+)?$")


def pinned(ref: str) -> bool:
    """A ref that names one revision: a full commit sha or a version tag. A branch,
    or no ref at all, follows whatever lands there."""
    return bool(ref) and bool(_SHA.match(ref) or _TAG.match(ref))
