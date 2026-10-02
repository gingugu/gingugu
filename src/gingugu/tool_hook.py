"""``gingugu hook tool`` - the PreToolUse entry point for tripwires and minions.

Inside a subagent the minion fence (``minion_fence``) is asked first, and it
runs even with tripwires switched off: the two are separate switches. A call
that spawns a subagent has its task prompt stashed for the SubagentStart
warm-up (``subagent_hook``), which is never told what the task is.

Claude Code runs this before every tool call and waits for it. The only way
to speak BEFORE a call runs is to deny it: a PreToolUse ``additionalContext``
is delivered next to the tool's result, which is after the damage. So the
first matching call in a session is denied with the memory as the reason, and
the agent either changes course or re-issues the call, which then passes. The
memory is in context by then; firing again would only block the retry.

Like the prompt hook, it must never break a session. Every failure exits 0
with no output, and no output means the normal permission flow applies.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .prompt_hook import LOG_BUSY_TIMEOUT_S, namespaces_for

# Our own memory tools are how a tripwire gets fixed. Guarding them could lock
# the agent out of repairing a bad one.
_SELF_PREFIX = "mcp__gingugu__"

_STATE_TTL_SECONDS = 7 * 24 * 3600


def enabled() -> bool:
    return os.environ.get("MEMORY_TRIPWIRES", "on").lower() not in ("0", "off", "false")


def _state_path(db_path: Path, session_id: str) -> Path:
    # Its own file beside the prompt hook's: each hook rewrites its file whole,
    # so sharing one would let either erase the other's state.
    from .subagent_hook import safe_session

    return db_path.parent / "hook-sessions" / f"tripwires-{safe_session(session_id)}.json"


def load_tripped(db_path: Path, session_id: str) -> set[str]:
    try:
        return set(json.loads(_state_path(db_path, session_id).read_text()).get("tripped", []))
    except (OSError, ValueError):
        return set()


def save_tripped(db_path: Path, session_id: str, ids: set[str]) -> None:
    path = _state_path(db_path, session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"tripped": sorted(ids), "updated": time.time()}))
        cutoff = time.time() - _STATE_TTL_SECONDS
        for entry in path.parent.glob("tripwires-*.json"):
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def _log_trip(db_path: Path, session_id: str, text: str, ids: list[str], ns: list[str]) -> None:
    """One ``query_log`` row per trip. Misses are not logged: this runs on
    every tool call, and a row per call would bury the trips in noise."""
    import sqlite3

    from .query_log import record
    from .recall_sweep import sqlite_uri

    try:
        conn = sqlite3.connect(sqlite_uri(db_path, "rw"), uri=True, timeout=LOG_BUSY_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - logging must never cost the call its warning
        return
    try:
        if record(
            conn, tool="tripwire", query=text, result_ids=ids, namespaces=ns, session_id=session_id
        ):
            conn.commit()
    except Exception:  # noqa: BLE001
        pass
    finally:
        conn.close()


def _deny_minion(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"gingugu minion fence: {reason}.",
                }
            }
        )
    )


def _minion_and_spawn(payload: dict) -> bool:
    """Fence a subagent's call, or stash a spawn's prompt. True = denied."""
    from . import minion_fence, subagent_hook
    from .config import load_config

    if payload.get("agent_id"):
        if not minion_fence.enabled():
            return False
        reason = minion_fence.decide(payload, load_config().db_path.parent)
        if reason:
            _deny_minion(reason)
            return True
        return False
    if payload.get("tool_name") in subagent_hook.SPAWN_TOOLS:
        subagent_hook.stash_spawn(load_config().db_path, payload)
    return False


def _emit(reason: str, count: int) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
                "systemMessage": f"gingugu: tripwire stopped a call ({count} memor"
                + ("y" if count == 1 else "ies")
                + ")",
            }
        )
    )


def run(payload: dict) -> int:
    """Decide and print. Returns the process exit code (always 0)."""
    if _minion_and_spawn(payload):
        return 0
    if not enabled():
        return 0
    tool_name = payload.get("tool_name") or ""
    if not tool_name or tool_name.startswith(_SELF_PREFIX):
        return 0
    tool_input = payload.get("tool_input")
    session_id = payload.get("session_id") or "unknown"
    cwd = payload.get("cwd") or os.getcwd()

    from .config import load_config
    from .recall_sweep import connect_readonly
    from .tripwire import load_tripwires, match_text, matching, render_reason

    app = load_config()
    if not app.db_path.exists():
        return 0

    namespaces = namespaces_for(cwd)
    conn = connect_readonly(app.db_path)
    try:
        wires = load_tripwires(conn, namespaces)
    except Exception:  # noqa: BLE001 - an unmigrated store has no table yet
        return 0
    finally:
        conn.close()

    tripped = load_tripped(app.db_path, session_id)
    # Filtered before matching, not after: the cap must count fresh trips only.
    live = [w for w in wires if w.memory_id not in tripped]
    hits = matching(live, tool_name, tool_input)
    if not hits:
        return 0

    ids = [h.memory_id for h in hits]
    _log_trip(app.db_path, session_id, match_text(tool_name, tool_input), ids, namespaces)
    save_tripped(app.db_path, session_id, tripped | set(ids))
    _emit(render_reason(hits), len(hits))
    return 0


def main() -> int:
    """Never raises. A hook that crashes on a tool call is worse than no hook."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        return run(payload)
    except Exception:
        return 0
