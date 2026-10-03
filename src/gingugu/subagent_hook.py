"""``gingugu hook subagent`` - warm a minion before its first turn.

A subagent starts cold. Its own stdio server is fenced by ``MEMORY_GRANT`` in
its agent file, so it *can* recall - this makes the context arrive without
being asked for, the way SessionStart does for the main thread.

SubagentStart can inject context but is never told the task. The spawning
``Agent`` call is, in the parent's PreToolUse a moment earlier, so
``gingugu hook tool`` calls ``stash`` there and ``run`` here ``take``s it back
as the task hint, matched on session and agent type. Two parallel spawns of
one type may swap hints; both share one grant, so that costs ranking, never
the fence.

Read-only end to end: the connection is opened read-only and the grant is
bound around the ranking, so the warm-up sees exactly what the minion's own
server would. An agent file without a grant gets nothing - its subagent still
holds the inherited full brain, and dumping that into it is not warm-up.

Like every hook here, it never breaks a session: each failure exits 0 silent.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from .subagent_warmup import warmup  # noqa: F401 - re-exported: callers import it from here

SPAWN_TOOLS = frozenset({"Agent", "Task"})
STASH_TTL_SECONDS = 300
_HINT_CHARS = 2000

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_NAME = re.compile(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.MULTILINE)
# The grant counts only as an indented env key inside the ``mcpServers`` block -
# the same place the server reads it from - never a description or comment.
_SERVERS = re.compile(r"^mcpServers:[ \t]*\n((?:[ \t-].*(?:\n|$)|[ \t]*(?:\n|$))*)", re.MULTILINE)
_GRANT = re.compile(
    r"^[ \t]+['\"]?MEMORY_GRANT['\"]?\s*:\s*['\"]?([^'\"\n#]+?)['\"]?\s*$", re.MULTILINE
)
_SAFE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")


def safe_session(session_id: str) -> str:
    """A session id fit for a filename. The harness sends UUIDs; anything else
    is hashed rather than trusted to stay inside the state directory."""
    if not session_id:
        return "unknown"
    if _SAFE_ID.fullmatch(session_id):
        return session_id
    import hashlib

    return hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()[:32]


def _stash_path(db_path: Path, session_id: str) -> Path:
    return db_path.parent / "hook-sessions" / f"spawns-{safe_session(session_id)}.json"


def _load(path: Path) -> list[dict]:
    try:
        entries = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _save(path: Path, entries: list[dict]) -> None:
    """Write the stash owner-only: it holds task prompts, which can carry
    anything the user typed into a delegation."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(entries))
        os.chmod(path, 0o600)  # an older, looser file keeps its mode on O_CREAT
    except OSError:
        pass


def _sweep(directory: Path, now: float) -> None:
    """Drop stash files no live spawn can still claim."""
    try:
        for entry in directory.glob("spawns-*.json"):
            if now - entry.stat().st_mtime > STASH_TTL_SECONDS:
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def stash(db_path: Path, payload: dict) -> None:
    """Remember a spawn's task prompt for the SubagentStart that follows."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    prompt, agent_type = tool_input.get("prompt"), tool_input.get("subagent_type")
    if not isinstance(prompt, str) or not prompt or not isinstance(agent_type, str):
        return
    path = _stash_path(db_path, str(payload.get("session_id") or ""))
    now = time.time()
    _sweep(path.parent, now)
    entries = [e for e in _load(path) if now - float(e.get("t", 0)) < STASH_TTL_SECONDS]
    entries.append({"type": agent_type, "prompt": prompt[:_HINT_CHARS], "t": now})
    _save(path, entries)


def take(db_path: Path, session_id: str, agent_type: str) -> str | None:
    """Pop the oldest live prompt stashed for this session and agent type."""
    path = _stash_path(db_path, session_id)
    now = time.time()
    entries = [e for e in _load(path) if now - float(e.get("t", 0)) < STASH_TTL_SECONDS]
    for i, entry in enumerate(entries):
        if entry.get("type") == agent_type:
            del entries[i]
            _save(path, entries)
            return str(entry.get("prompt") or "") or None
    _save(path, entries)
    return None


def _roots(payload: dict) -> list[Path]:
    project = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    return [Path(project), Path.home()]


def stash_spawn(db_path: Path, payload: dict) -> None:
    """Stash a spawn's prompt only when the agent being spawned is fenced -
    nothing else gets a warm-up, so nothing else's prompt needs to touch disk."""
    tool_input = payload.get("tool_input")
    agent_type = tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
    if isinstance(agent_type, str) and agent_grant(agent_type, _roots(payload)):
        stash(db_path, payload)


def agent_grant(agent_type: str, roots: list[Path]) -> str | None:
    """The ``MEMORY_GRANT`` in the agent file named ``agent_type``, if any.

    Matched on the frontmatter ``name``, not the filename, and only inside the
    frontmatter. The first root holding a matching file wins.
    """
    for root in roots:
        agents = root / ".claude" / "agents"
        try:
            files = sorted(agents.glob("*.md"))
        except OSError:
            continue
        for path in files:
            try:
                head = _FRONTMATTER.match(path.read_text())
            except OSError:
                continue
            if head is None:
                continue
            name = _NAME.search(head.group(1))
            if name is None or name.group(1).strip() != agent_type:
                continue
            servers = _SERVERS.search(head.group(1))
            grant = _GRANT.search(servers.group(1)) if servers else None
            return grant.group(1).strip() if grant else None
    return None


def _emit(context: str) -> None:
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": context}}
        )
    )


def run(payload: dict) -> int:
    """Decide and print. Returns the process exit code (always 0)."""
    agent_type = payload.get("agent_type")
    if not isinstance(agent_type, str) or not agent_type:
        return 0
    from .config import load_config

    app = load_config()
    session_id = str(payload.get("session_id") or "unknown")
    hint = take(app.db_path, session_id, agent_type)
    spec = agent_grant(agent_type, _roots(payload))
    if not spec:
        return 0
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()

    from . import hook_remote

    target = hook_remote.target()
    if target is not None:
        # The brain ranks, with its warm model; nothing local is opened.
        from .hook_remote_flows import remote_warmup

        context = remote_warmup(target, spec, hint, agent_type, cwd)
        if context:
            _emit(context)
        return 0
    if not app.db_path.exists():
        return 0

    embedder = None
    if app.embeddings_enabled and hint:
        from .embeddings import build_provider

        embedder = build_provider(
            app.embeddings_enabled,
            model_name=app.embeddings_model,
            backend=app.embeddings_backend,
            ollama_host=app.embeddings_ollama_host,
            ollama_model=app.embeddings_ollama_model,
        )
    context = warmup(app.db_path, spec, hint, agent_type=agent_type, embedder=embedder, cwd=cwd)
    if context:
        _emit(context)
    return 0


def main() -> int:
    """Never raises. A hook that crashes a spawn is worse than a cold minion."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        return run(payload)
    except Exception:
        return 0
