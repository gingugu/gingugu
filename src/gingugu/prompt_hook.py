"""``gingugu hook prompt`` - the UserPromptSubmit entry point for involuntary recall.

Claude Code runs this before it processes a prompt, on every turn, and waits
for it. Two consequences shape the whole module:

- It must never break a session. Every failure path exits 0 with no output, so
  the worst outcome of any bug in here is that memory stays quiet.
- It must be cheap on the common turn. The length gate runs before the encoder
  is imported, so an acknowledgement costs a process spawn and nothing else -
  roughly 44% of real prompts never pay for the model at all.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from .recall_gate import GateConfig, is_worth_embedding, render, strip_affect

GLOBAL_NAMESPACE = "crow"
_PERSONA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # also the harness's namespace rule

# How long logging the prompt may wait on a busy database. The hook holds the
# user's turn, so losing one row beats a visible stall.
LOG_BUSY_TIMEOUT_S = 0.5

# Suppression state older than this is a session that ended. Pruned on write so
# the directory cannot grow without bound.
_STATE_TTL_SECONDS = 7 * 24 * 3600


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def config_from_env() -> GateConfig:
    """Gate tuning, overridable without a reinstall.

    The defaults are the measured ones; these exist so a sweep in ``bench`` can
    be reproduced live without editing an installed file.
    """
    base = GateConfig()
    return GateConfig(
        bar=_env_float("MEMORY_RECALL_HOOK_BAR", base.bar),
        margin=_env_float("MEMORY_RECALL_HOOK_MARGIN", base.margin),
        cap=_env_int("MEMORY_RECALL_HOOK_CAP", base.cap),
        min_chars=_env_int("MEMORY_RECALL_HOOK_MIN_CHARS", base.min_chars),
        require_lexical=os.environ.get("MEMORY_RECALL_HOOK_LEXICAL", "1") != "0",
        types=base.types,
    )


def enabled() -> bool:
    return os.environ.get("MEMORY_RECALL_HOOK", "on").lower() not in ("0", "off", "false")


def _state_dir(db_path: Path) -> Path:
    return db_path.parent / "hook-sessions"


def load_suppressed(db_path: Path, session_id: str) -> set[str]:
    """Ids already injected in this session.

    Without this a memory that matches a topic re-surfaces on every turn about
    that topic, which is how an unrequested signal turns into a stutter and
    trains the reader to skip the block entirely.
    """
    from .subagent_hook import safe_session

    path = _state_dir(db_path) / f"{safe_session(session_id)}.json"
    try:
        return set(json.loads(path.read_text()).get("injected", []))
    except (OSError, ValueError):
        return set()


def save_suppressed(db_path: Path, session_id: str, ids: set[str]) -> None:
    from .subagent_hook import safe_session

    directory = _state_dir(db_path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{safe_session(session_id)}.json").write_text(
            json.dumps({"injected": sorted(ids), "updated": time.time()})
        )
        _prune(directory)
    except OSError:
        pass  # state is an optimisation, never a requirement


def _prune(directory: Path) -> None:
    cutoff = time.time() - _STATE_TTL_SECONDS
    try:
        for entry in directory.glob("*.json"):
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def persona() -> str | None:
    """This agent's own namespace from MEMORY_PERSONA, or None.

    crow is shared by every persona; a persona's self lives in its own
    namespace. A malformed value is ignored rather than raised: a hook must
    never break a session.
    """
    raw = os.environ.get("MEMORY_PERSONA", "").strip()
    if not _PERSONA_RE.fullmatch(raw) or raw == GLOBAL_NAMESPACE:
        return None
    return raw


def namespaces_for(cwd: str) -> list[str]:
    """The global namespace, this agent's persona if set, then this repo's.

    Same derivation the SessionStart hook uses: the directory name, so the
    hook is portable into any repo without configuration.
    """
    names = [GLOBAL_NAMESPACE]
    for name in (persona(), Path(cwd).name):
        if name and name not in names:
            names.append(name)
    return names


def _emit(context: str, count: int) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                    "systemMessage": f"gingugu: {count} memor"
                    + ("y" if count == 1 else "ies")
                    + " surfaced",
                }
            }
        )
    )


def log_prompt(
    db_path: Path, session_id: str, query: str, result_ids: list[str], namespaces: list[str]
) -> None:
    """Record the prompt this turn searched on, and what it injected.

    The sweep's connection is read-only by design, so this opens its own write
    connection, briefly, after the sweep is done. It is the one write the hook
    makes to the brain, and it is best-effort twice over: a busy database waits
    at most ``LOG_BUSY_TIMEOUT_S`` rather than holding the turn, and a missing
    file or a store not yet migrated to ``query_log`` is skipped, never created.
    Every failure is swallowed, not just database ones: this runs before the
    injection, and a logging bug must not cost the turn its recall.
    ``session_id`` is Claude Code's, not an MCP session id, so these rows do not
    join to ``access_log`` - ``tool='hook'`` keeps them apart.
    """
    import sqlite3

    from .query_log import record
    from .recall_sweep import sqlite_uri

    try:
        conn = sqlite3.connect(sqlite_uri(db_path, "rw"), uri=True, timeout=LOG_BUSY_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - logging must never cost the turn its recall
        return
    try:
        if record(
            conn,
            tool="hook",
            query=query,
            result_ids=result_ids,
            namespaces=namespaces,
            session_id=session_id,
        ):
            conn.commit()
    except Exception:  # noqa: BLE001
        pass
    finally:
        conn.close()


def run(payload: dict) -> int:
    """Decide and print. Returns the process exit code (always 0)."""
    if not enabled():
        return 0

    prompt = payload.get("prompt") or ""
    session_id = payload.get("session_id") or "unknown"
    cwd = payload.get("cwd") or os.getcwd()
    cfg = config_from_env()

    # Stage one, before any expensive import.
    if not is_worth_embedding(prompt, min_chars=cfg.min_chars):
        return 0

    from .config import load_config

    app = load_config()
    namespaces = namespaces_for(cwd)

    from . import hook_remote

    target = hook_remote.target()
    if target is not None:
        # The brain embeds and ranks; this machine keeps only suppression, so a
        # memory already shown this session is not asked for again. Never local.
        from .hook_remote_flows import remote_recall

        suppressed = load_suppressed(app.db_path, session_id)
        found = remote_recall(target, prompt, session_id, namespaces, suppressed, cfg)
        if found is not None:
            context, ids = found
            _emit(context, len(ids))
            save_suppressed(app.db_path, session_id, suppressed | set(ids))
        return 0

    from .embeddings import build_provider
    from .recall_pick import pick

    if not app.embeddings_enabled or not app.db_path.exists():
        return 0

    provider = build_provider(
        app.embeddings_enabled,
        model_name=app.embeddings_model,
        backend=app.embeddings_backend,
        ollama_host=app.embeddings_ollama_host,
        ollama_model=app.embeddings_ollama_model,
    )
    cleaned = strip_affect(prompt)[:2000]
    suppressed = load_suppressed(app.db_path, session_id)
    picked = pick(app.db_path, provider, cleaned, namespaces, cfg, suppressed, session_id)
    if not picked:
        return 0

    _emit(render(picked), len(picked))
    save_suppressed(app.db_path, session_id, suppressed | {c.id for c in picked})
    return 0


def main() -> int:
    """Never raises. A hook that crashes on a keystroke is worse than no hook."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        return run(payload)
    except Exception:
        return 0
