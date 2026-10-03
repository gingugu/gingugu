"""The minion fence: what a subagent may not do to the brain behind its server.

A warm minion reaches the brain through its own stdio server, fenced by
``MEMORY_GRANT``. That fence is the server's, so it holds - for the memory
tools. It says nothing about the other ways a subagent can touch the same
bytes, and this module shuts those, decided per tool call by the PreToolUse
hook (``gingugu hook tool``):

- **File tools** on the brain's data directory (the database, the owner token,
  the scoped-token table). The path is a parameter, so this check holds.
- **Shell commands** that name the database, ``sqlite3``, the data directory or
  the ``gingugu`` CLI. A speed bump, not a wall: a command can build a path the
  pattern never sees. It stops the accident, which is the threat here.
- **Write tools of the inherited ``gingugu`` server.** Every subagent inherits
  its parent's MCP servers, so a built-in agent (Explore, general-purpose) with
  no agent file of ours holds the full brain. Reads stay open; writes do not.

Claude Code sets ``agent_id`` only on a call made inside a subagent, so the
main thread is never fenced here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_INHERITED = "mcp__gingugu__"

# Tools of the inherited full-brain server that change it, move it, or reach
# the credential vault. Keyed by bare tool name; a set means only those
# ``action`` values write.
_WRITES: dict[str, set[str] | None] = {
    "memory_store": None,
    "memory_update": None,
    "memory_forget": None,
    "memory_relate": None,
    "memory_unrelate": None,
    "memory_consolidate": None,
    "memory_import": None,
    "memory_export": None,
    "memory_dream": None,
    "memory_tripwire": {"add", "remove"},
    "memory_namespaces": {"create", "update", "delete"},
}

_FILE_PARAMS = ("file_path", "notebook_path", "path")
_SHELL_TOOLS = frozenset({"Bash", "PowerShell", "Monitor"})

# Where a shell command starts: the line, or after a separator or an opening
# paren/backtick. ``gingugu`` counts only in command position there - the first
# word (env assignments skipped), a ``.../bin/gingugu`` path, or the program a
# launcher runs (``uv run``, ``uvx``, ``python -m``). The same word in an echo, a
# commit message or a ``cd`` path is prose, not the CLI.
_SEGMENTS = re.compile(r"[;&|()`\n]+")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_LAUNCHERS = {"uv", "uvx", "python", "python3", "exec", "nohup", "env", "time"}
_SHELL_WORDS = re.compile(r"\bsqlite3\b|memories\.db")


def enabled() -> bool:
    return os.environ.get("MEMORY_MINION_FENCE", "on").lower() not in ("0", "off", "false")


def _real(path: str, cwd: str) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(cwd) / p
    return Path(os.path.realpath(p))


def _inside(path: str, cwd: str, data_dir: Path) -> bool:
    return _real(path, cwd).is_relative_to(Path(os.path.realpath(data_dir)))


_SEARCH_TOOLS = frozenset({"Grep", "Glob"})
_WILDCARDS = re.compile(r"[*?\[{]")


def _rooted(pattern: str) -> bool:
    """Whether ``pattern`` names a place on disk rather than one under cwd.

    Since 3.13, ``os.path.isabs`` on Windows is False for ``\\Users\\...``, a
    path that still resolves against the current drive and so reaches the
    data dir; a leading separator counts as rooted on every version.
    """
    expanded = os.path.expanduser(pattern)
    return os.path.isabs(expanded) or expanded.startswith(("/", os.sep))


def _search_root(tool_input: dict, cwd: str) -> str:
    """Where a Grep/Glob starts: its ``path``, the fixed prefix of an absolute
    glob pattern, else the working directory."""
    path = tool_input.get("path")
    if isinstance(path, str) and path:
        return path
    pattern = tool_input.get("pattern")
    if isinstance(pattern, str) and _rooted(pattern):
        head = _WILDCARDS.split(os.path.expanduser(pattern), 1)[0]
        return head if head.endswith(os.sep) or not head else os.path.dirname(head)
    return cwd


def _covers(root: str, cwd: str, data_dir: Path) -> bool:
    """Whether a recursive search from ``root`` reaches into ``data_dir``."""
    return Path(os.path.realpath(data_dir)).is_relative_to(_real(root or os.sep, cwd))


def _file_reason(tool_input: dict, cwd: str, data_dir: Path, *, search: bool) -> str | None:
    # A search rooted above the data dir recurses into it, so for Grep/Glob an
    # ancestor of the data dir is as closed as the data dir itself.
    if search and _covers(_search_root(tool_input, cwd), cwd, data_dir):
        return "a search reaching into the brain's data directory is closed to minions"
    paths = [tool_input.get(k) for k in _FILE_PARAMS]
    pattern = tool_input.get("pattern")
    # Glob takes an absolute pattern as well as a path; Grep's pattern is a
    # regex, never a path, but an absolute one cannot be a regex we care about.
    if isinstance(pattern, str) and _rooted(pattern):
        paths.append(pattern)
    for value in paths:
        if isinstance(value, str) and value and _inside(value, cwd, data_dir):
            return "the brain's data directory is closed to minions"
    return None


def _is_cli(word: str) -> bool:
    return word.strip("'\"").rsplit("/", 1)[-1] == "gingugu"


def _runs_cli(command: str) -> bool:
    """Whether any segment of ``command`` runs the gingugu CLI."""
    for segment in _SEGMENTS.split(command):
        words = [w for w in segment.split() if w]
        while words and _ASSIGN.match(words[0]):
            words.pop(0)
        if not words:
            continue
        if _is_cli(words[0]):
            return True
        if words[0].rsplit("/", 1)[-1] in _LAUNCHERS:
            # The launched program is the first word after the launcher's own
            # options: skip ``-m``/``run`` and flag/value pairs like --project X.
            rest = words[1:]
            while rest and (rest[0].startswith("-") or rest[0] in ("run", "-m")):
                takes_value = rest[0].startswith("--") and "=" not in rest[0]
                rest = rest[2:] if takes_value and rest[0] not in ("--",) else rest[1:]
            if rest and _is_cli(rest[0]):
                return True
    return False


def _shell_reason(command: str, data_dir: Path) -> str | None:
    dirs = {str(data_dir), os.path.realpath(data_dir)}
    home = str(Path.home())
    for d in list(dirs):
        if d.startswith(home + os.sep):
            dirs.add("~" + d[len(home) :])
    if any(d in command for d in dirs) or _SHELL_WORDS.search(command) or _runs_cli(command):
        return "shell access to the brain's database or the gingugu CLI is closed to minions"
    return None


def _mcp_reason(tool_name: str, tool_input: dict) -> str | None:
    bare = tool_name[len(_INHERITED) :]
    if bare.startswith("credential_"):
        return "the credential vault is closed to minions"
    if bare not in _WRITES:
        return None
    actions = _WRITES[bare]
    if actions is not None and str(tool_input.get("action") or "list") not in actions:
        return None
    return (
        f"{bare} on the inherited full-brain server is closed to minions; "
        "write findings through your own fenced gingugu server"
    )


def decide(payload: dict, data_dir: Path) -> str | None:
    """Why this subagent call is refused, or None to let it through."""
    if not payload.get("agent_id"):
        return None
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cwd = str(payload.get("cwd") or os.getcwd())
    try:
        if tool_name.startswith(_INHERITED):
            return _mcp_reason(tool_name, tool_input)
        if tool_name in _SHELL_TOOLS:
            command = tool_input.get("command")
            return _shell_reason(command, data_dir) if isinstance(command, str) else None
        return _file_reason(tool_input, cwd, data_dir, search=tool_name in _SEARCH_TOOLS)
    except (OSError, ValueError):
        return None
