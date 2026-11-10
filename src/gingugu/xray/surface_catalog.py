"""X7d: the closed vocabularies of the attack-surface map - data, no logic to speak of.

A *sink* is an external call that does something an attacker would want done:
run a command, evaluate code, rebuild an object from bytes, change the disk,
talk to the network, read a secret. An *entry* is where outside input arrives:
an HTTP route, an MCP tool, a CLI command, a queued task.

Both are named catalogues, per language, and nothing outside them is ever
stored: a sink or an entry label reaches the agent's context, so the cache may
hold only names written here (or, for a label, identifiers). A name missing
from a catalogue is a sink the map does not see - the caveat says so.

The language key is the adapter's: "python", "typescript" (TS and JS), "go".
"""

from __future__ import annotations

# Most dangerous first: the order the map is read in.
CATEGORIES = ("exec", "eval", "deserialize", "sql", "fs", "secret", "net", "db")
KINDS = ("http", "mcp", "cli", "task", "main")
SQL_BUILT = "sql.built"  # a query string assembled at runtime (walkers flag it)
MAX_LABEL = 80


def _names(category: str, module: str, names: str) -> dict[str, str]:
    return {f"{module}.{n}" if module else n: category for n in names.split()}


_PY: dict[str, str] = {
    **_names(
        "exec", "subprocess", "run Popen call check_call check_output getoutput getstatusoutput"
    ),
    **_names("exec", "os", "system popen execv execve execvp execl spawnv spawnl posix_spawn"),
    **_names("exec", "asyncio", "create_subprocess_exec create_subprocess_shell"),
    **_names("exec", "pty", "spawn"),
    **_names("eval", "", "eval exec compile __import__"),
    **_names("eval", "importlib", "import_module"),
    **_names("deserialize", "pickle", "load loads"),
    **_names("deserialize", "marshal", "load loads"),
    **_names("deserialize", "dill", "load loads"),
    **_names("deserialize", "yaml", "load unsafe_load full_load"),
    **_names("deserialize", "shelve", "open"),
    **_names("fs", "", "open"),
    **_names("fs", "os", "remove unlink rmdir removedirs rename replace chmod chown"),
    **_names("fs", "shutil", "rmtree move copy copy2 copyfile copytree"),
    **_names("fs", "tempfile", "mktemp"),
    **_names("secret", "keyring", "get_password set_password delete_password"),
    **_names("net", "urllib.request", "urlopen urlretrieve"),
    **_names("net", "requests", "get post put patch delete head request"),
    **_names("net", "httpx", "get post put patch delete head request stream"),
    **_names("net", "socket", "create_connection socket"),
}

_FS_JS = "writeFile writeFileSync appendFile appendFileSync unlink unlinkSync rm rmSync rmdir "
_FS_JS += "rmdirSync rename renameSync chmod chmodSync chown chownSync createWriteStream"
_TS: dict[str, str] = {
    **_names("exec", "child_process", "exec execSync spawn spawnSync execFile execFileSync fork"),
    **_names("eval", "", "eval Function"),
    **_names("eval", "vm", "runInContext runInNewContext runInThisContext compileFunction"),
    **_names("deserialize", "node-serialize", "unserialize"),
    **_names("fs", "fs", _FS_JS),
    **_names("fs", "fs/promises", "writeFile appendFile unlink rm rmdir rename chmod chown"),
    **_names("net", "", "fetch"),
    **_names("net", "axios", "get post put patch delete head request"),
    **_names("net", "http", "request get"),
    **_names("net", "https", "request get"),
}

_GO: dict[str, str] = {
    **_names("exec", "os/exec", "Command CommandContext"),
    **_names("exec", "syscall", "Exec ForkExec StartProcess"),
    **_names("exec", "os", "StartProcess"),
    **_names("eval", "plugin", "Open"),
    **_names("deserialize", "encoding/gob", "NewDecoder"),
    **_names("fs", "os", "Remove RemoveAll Rename WriteFile Create OpenFile Chmod Chown"),
    **_names("fs", "io/ioutil", "WriteFile"),
    **_names("net", "net/http", "Get Post PostForm Head NewRequest NewRequestWithContext"),
    **_names("net", "net", "Dial DialTimeout"),
}

SINKS: dict[str, dict[str, str]] = {"python": _PY, "typescript": _TS, "go": _GO}
_ALL: dict[str, str] = {SQL_BUILT: "sql", **_PY, **_TS, **_GO}


def sink(lang: str, dotted: str) -> str | None:
    """The category of the external call ``dotted`` in ``lang``; None: not a sink."""
    return SINKS.get(lang, {}).get(dotted)


def category(name: str) -> str | None:
    """The category of a stored sink name, in any language; None: never written here."""
    return _ALL.get(name) if isinstance(name, str) else None


# --- entries --------------------------------------------------------------------------

_HTTP = frozenset("get post put patch delete head options route api_route websocket".split())
_PY_LAST = {
    **dict.fromkeys(_HTTP, "http"),
    **dict.fromkeys(("tool", "resource", "prompt"), "mcp"),
    **dict.fromkeys(("command", "group", "callback"), "cli"),
    "task": "task",
}
# Called with a handler, not used as a decorator: the last name decides.
_PY_REGISTRARS = {
    **dict.fromkeys("add_url_rule add_api_route add_route Route path re_path".split(), "http"),
    "add_tool": "mcp",
    "set_defaults": "cli",  # argparse: set_defaults(func=handler)
}
# Single names that mean something with no receiver: a decorator, or the walk's own
# label for a script's `if __name__ == "__main__":` block.
_PY_BARE = {"shared_task": "task", "__main__": "main"}
_JS_VERBS = _HTTP - {"api_route", "websocket"} | {"all"}
JS_RECEIVERS = frozenset({"app", "router", "server", "fastify", "hono"})
_JS_MCP_RECV = frozenset({"server", "mcp", "mcpServer"})
_JS_MCP = frozenset({"tool", "resource", "prompt"})
_JS_REGISTER = frozenset({"registerTool", "registerResource", "registerPrompt"})
TS_DECOS = frozenset("Get Post Put Patch Delete All Head Options".split())  # NestJS
_GO_LAST = frozenset(
    "HandleFunc Handle Method MethodFunc GET POST PUT PATCH DELETE Get Post Put Patch Delete "
    "Any".split()
)
# Method names that run a query: a built string handed to one is flagged.
SQL_METHODS: dict[str, frozenset[str]] = {
    "python": frozenset({"execute", "executemany", "executescript", "raw", "text"}),
    "typescript": frozenset(
        {"query", "execute", "exec", "raw", "prepare", "$queryRawUnsafe", "$executeRawUnsafe"}
    ),
    "go": frozenset(
        "Query QueryRow Exec Prepare QueryContext QueryRowContext ExecContext "
        "PrepareContext Raw".split()
    ),
}


def is_label(label: object) -> bool:
    """A dotted identifier chain short enough to print: what a walker may store."""
    if not isinstance(label, str) or not 0 < len(label) <= MAX_LABEL:
        return False
    return all(p.replace("$", "_").isidentifier() for p in label.split("."))


def router_shaped(name: str | None) -> bool:
    """A JS receiver a route call is made on: a known server name, or one named
    for a router or an app (``usersRouter``, ``adminApp``) - never an HTTP client."""
    return name is not None and (
        name in JS_RECEIVERS or name.endswith(("Router", "router", "App", "app"))
    )


def py_registrar(label: str) -> bool:
    """Whether a Python CALL to ``label`` registers a handler. A verb only makes
    an entry as a decorator: ``app.get(...)`` as a plain call is ``dict.get``."""
    return is_label(label) and label.rsplit(".", 1)[-1] in _PY_REGISTRARS


def entry_kind(lang: str, label: str) -> str | None:
    """What kind of entry the decorator or registrar ``label`` makes; None: not one.

    Python: ``mcp.tool``, ``app.get``, ``cli.command``, ``celery.task`` - the last
    name decides, and a receiver is required (a bare ``get`` is anything); the
    registrars (``add_url_rule``, ``path``, ``set_defaults``) take a handler.
    TS/JS: an HTTP verb on a receiver named like a server (``app``, ``router``),
    an MCP ``server.tool``/``registerTool``, a NestJS ``@Get``.
    Go: ``HandleFunc``/``Handle`` and the router verbs on any receiver; ``main``."""
    if not is_label(label):
        return None
    parts = label.split(".")
    last, recv = parts[-1], parts[-2] if len(parts) > 1 else None  # app.get, this.app.get
    if lang == "python":
        if last in _PY_REGISTRARS:
            return _PY_REGISTRARS[last]
        if len(parts) == 1:
            return _PY_BARE.get(last)
        return _PY_LAST.get(last)
    if lang == "typescript":
        if len(parts) == 1:
            return "http" if last in TS_DECOS else None
        if router_shaped(recv) and last in _JS_VERBS:
            return "http"
        if (recv in _JS_MCP_RECV and last in _JS_MCP) or (recv and last in _JS_REGISTER):
            return "mcp"
        return None
    if lang == "go":
        if label == "main":
            return "main"
        return "http" if recv and last in _GO_LAST else None
    return None
