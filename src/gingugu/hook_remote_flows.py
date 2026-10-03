"""What each hook does when the brain is remote.

One function per round trip, so the hooks keep their local shape and only branch
at the point they would have opened the local database. None of these opens it,
loads an encoder, or falls back to local on a failure: the answer to a brain that
cannot be reached is silence.

Tripwire rules are the exception to "ask every time". ``PreToolUse`` fires on
every tool call, and a network round trip per call is a tax on the whole
session, so the rules are cached beside the other hook state for a minute and
the last copy is kept for when the brain is away. Matching stays local; only a
trip is reported upstream.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
from pathlib import Path

from . import hook_remote
from .hook_remote import RemoteTarget

RULES_TTL_S = 60.0
# What query_log keeps of a trip's text anyway; the rest never leaves the machine.
TRIP_TEXT_CHARS = 2000


def remote_recall(
    target: RemoteTarget,
    prompt: str,
    session_id: str,
    namespaces: list[str],
    suppressed: set[str],
    cfg,
) -> tuple[str, list[str]] | None:
    """The brain's ``(context, ids)`` for this prompt, or None if it has none."""
    reply = hook_remote.post(
        target,
        "/hook/recall",
        {
            "prompt": prompt[:4000],
            "session_id": session_id,
            "namespaces": namespaces,
            "suppressed": sorted(suppressed),
            "config": {
                "bar": cfg.bar,
                "margin": cfg.margin,
                "cap": cfg.cap,
                "require_lexical": cfg.require_lexical,
            },
        },
        timeout=hook_remote.RECALL_TIMEOUT_S,
    )
    if not isinstance(reply, dict):
        return None
    context, ids = reply.get("context"), reply.get("ids")
    if not isinstance(context, str) or not context:
        return None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return None
    return context, ids


def remote_warmup(
    target: RemoteTarget, spec: str, hint: str | None, agent_type: str, cwd: str
) -> str | None:
    """The brain's warm-up text for a fenced minion, or None."""
    from .prompt_hook import namespaces_for

    reply = hook_remote.post(
        target,
        "/hook/warmup",
        {
            "spec": spec,
            "task_hint": hint,
            "agent_type": agent_type,
            "namespaces": namespaces_for(cwd),
        },
        timeout=hook_remote.WARMUP_TIMEOUT_S,
    )
    context = reply.get("context") if isinstance(reply, dict) else None
    return context if isinstance(context, str) and context else None


# --- tripwires ---------------------------------------------------------------


def _cache_path(state_dir: Path, url: str, namespaces: list[str]) -> Path:
    key = hashlib.sha256((url + "\n" + ",".join(namespaces)).encode()).hexdigest()[:16]
    return state_dir / f"tripwire-rules-{key}.json"


def _parse(raw: object) -> list | None:
    """Rules from a wire list, or None if any entry is not a whole rule."""
    from .tripwire import Tripwire

    if not isinstance(raw, list):
        return None
    try:
        wires = [Tripwire(**d) for d in raw]
    except TypeError:  # a missing or unknown key, or an entry that is no dict
        return None
    # Every field is text; a number in a pattern would only fail later, mid-match.
    if not all(isinstance(v, str) for w in wires for v in dataclasses.astuple(w)):
        return None
    return wires


def _read_cache(path: Path) -> tuple[float, list] | None:
    try:
        data = json.loads(path.read_text())
        return float(data["fetched"]), data["tripwires"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(path: Path, wires: list) -> None:
    """Owner-only, like the spawn stash: rule text is the user's own memory."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # No following a link planted at this path onto some other file.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps({"fetched": time.time(), "tripwires": wires}))
        os.chmod(path, 0o600)  # an older, looser file keeps its mode on O_CREAT
    except OSError:
        pass  # the cache is an optimisation, never a requirement


def remote_rules(target: RemoteTarget, namespaces: list[str], state_dir: Path) -> list:
    """Tripwire rules for ``namespaces``: fresh cache, else the brain, else the
    stale cache, else none."""
    path = _cache_path(state_dir, target.url, namespaces)
    cached = _read_cache(path)
    if cached and 0 <= time.time() - cached[0] < RULES_TTL_S:
        return _parse(cached[1]) or []
    reply = hook_remote.post(
        target,
        "/hook/tripwires",
        {"namespaces": namespaces},
        timeout=hook_remote.TRIPWIRES_TIMEOUT_S,
    )
    fresh = _parse(reply.get("tripwires")) if isinstance(reply, dict) else None
    if fresh is not None:
        _write_cache(path, reply["tripwires"])
        return fresh
    return (_parse(cached[1]) if cached else None) or []


def report_trip(
    target: RemoteTarget, session_id: str, text: str, ids: list[str], namespaces: list[str]
) -> None:
    """Best-effort: the brain logs the trip; the reply is not needed."""
    hook_remote.post(
        target,
        "/hook/trip",
        {
            "session_id": session_id,
            "text": text[:TRIP_TEXT_CHARS],
            "ids": ids,
            "namespaces": namespaces,
        },
        timeout=hook_remote.TRIP_TIMEOUT_S,
    )
