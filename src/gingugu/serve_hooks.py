"""Remote-mode hook routes: ``/hook/recall``, ``/hook/tripwires``, ``/hook/trip``
and ``/hook/warmup`` (all POST, JSON object in, JSON object out).

They serve the machine's own hooks, which run with the main thread's full view,
so only an owner token may call them (403 otherwise, like ``/token/derive``).
The brain does the heavy part - embedding with its warm model, sweeping, ranking
- and keeps no session state: suppression and the client's namespaces arrive with
each request. The one write is the ``query_log`` row the hook would have made
locally.

Each route is a ``(parse, run)`` pair. ``parse`` validates on the event loop, so
a bad body costs no thread and writes no log row; ``run`` does the work in a
worker thread, on its own read-only connection. ``ctx.conn`` belongs to the MCP
handlers on the event loop and is never touched.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Callable

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse

from .serve import OWNER

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_NAMESPACES = 16
_MAX_IDS = 1000
_NUMBERS = ("bar", "margin")


class _BadRequest(ValueError):
    pass


# Generous for any real hook, small enough that a body cannot tie up a worker.
_MAX_CHARS = {
    "prompt": 8000,
    "text": 4000,
    "session_id": 256,
    "spec": 2000,
    "agent_type": 128,
    "task_hint": 4000,
}


def _text(body: dict, key: str, *, optional: bool = False, nonempty: bool = False) -> str | None:
    value = body.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or (nonempty and not value):
        raise _BadRequest(f"{key} must be a string")
    if len(value) > _MAX_CHARS[key]:
        raise _BadRequest(f"{key} is longer than {_MAX_CHARS[key]} characters")
    return value


def _names(body: dict) -> list[str]:
    value = body.get("namespaces")
    if not isinstance(value, list) or len(value) > _MAX_NAMESPACES:
        raise _BadRequest(f"namespaces must be a list of at most {_MAX_NAMESPACES} names")
    if not all(isinstance(n, str) and _NAME_RE.fullmatch(n) for n in value):
        raise _BadRequest("namespaces must be names of letters, digits, '.', '_' or '-'")
    return value


def _ids(body: dict, key: str) -> list[str]:
    value = body.get(key)
    if not isinstance(value, list) or len(value) > _MAX_IDS:
        raise _BadRequest(f"{key} must be a list of at most {_MAX_IDS} ids")
    if not all(isinstance(i, str) for i in value):
        raise _BadRequest(f"{key} must be a list of strings")
    return value


def _gate_config(body: dict):
    """The brain's gate defaults, overridden by whatever the client sent."""
    from .recall_gate import GateConfig

    sent = body.get("config")
    if sent is None:
        return GateConfig()
    if not isinstance(sent, dict) or not set(sent) <= {*_NUMBERS, "cap", "require_lexical"}:
        raise _BadRequest("config holds only bar, margin, cap and require_lexical")
    over: dict = {}
    for key in _NUMBERS:
        if key in sent:
            if isinstance(sent[key], bool) or not isinstance(sent[key], (int, float)):
                raise _BadRequest(f"config.{key} must be a number")
            over[key] = float(sent[key])
    if "cap" in sent:
        if isinstance(sent["cap"], bool) or not isinstance(sent["cap"], int):
            raise _BadRequest("config.cap must be an integer")
        over["cap"] = sent["cap"]
    if "require_lexical" in sent:
        if not isinstance(sent["require_lexical"], bool):
            raise _BadRequest("config.require_lexical must be a boolean")
        over["require_lexical"] = sent["require_lexical"]
    return dataclasses.replace(GateConfig(), **over)


# --- recall ------------------------------------------------------------------


def _parse_recall(body: dict) -> tuple:
    return (
        _text(body, "prompt"),
        _text(body, "session_id"),
        _names(body),
        set(_ids(body, "suppressed")),
        _gate_config(body),
    )


def _run_recall(ctx, args: tuple) -> dict:
    from .recall_gate import render, strip_affect
    from .recall_pick import pick

    prompt, session_id, namespaces, suppressed, cfg = args
    embedder = ctx.store.embedder
    if not embedder.enabled:
        return {"context": None, "ids": []}
    cleaned = strip_affect(prompt)[:2000]
    picked = pick(ctx.config.db_path, embedder, cleaned, namespaces, cfg, suppressed, session_id)
    if not picked:
        return {"context": None, "ids": []}
    return {"context": render(picked), "ids": [c.id for c in picked]}


# --- tripwires ---------------------------------------------------------------


def _parse_tripwires(body: dict) -> tuple:
    return (_names(body),)


def _run_tripwires(ctx, args: tuple) -> dict:
    from .recall_sweep import connect_readonly
    from .tripwire import load_tripwires

    conn = connect_readonly(ctx.config.db_path)
    try:
        wires = load_tripwires(conn, args[0])
    except Exception:  # noqa: BLE001 - an unmigrated store has no table yet
        wires = []
    finally:
        conn.close()
    return {"tripwires": [dataclasses.asdict(w) for w in wires]}


def _parse_trip(body: dict) -> tuple:
    return (_text(body, "session_id"), _text(body, "text"), _ids(body, "ids"), _names(body))


def _run_trip(ctx, args: tuple) -> dict:
    from .tool_hook import _log_trip

    session_id, text, ids, namespaces = args
    _log_trip(ctx.config.db_path, session_id, text, ids, namespaces)
    return {"ok": True}


# --- warm-up -----------------------------------------------------------------


def _parse_warmup(body: dict) -> tuple:
    return (
        _text(body, "spec"),
        _text(body, "task_hint", optional=True),
        _text(body, "agent_type", nonempty=True),
        _names(body),
    )


def _run_warmup(ctx, args: tuple) -> dict:
    from .subagent_warmup import warmup

    spec, hint, agent_type, scope = args
    embedder = ctx.store.embedder if hint and ctx.store.embedder.enabled else None
    context = warmup(
        ctx.config.db_path, spec, hint, agent_type=agent_type, embedder=embedder, scope=scope
    )
    return {"context": context}


# --- routing -----------------------------------------------------------------


def _endpoint(parse: Callable, run: Callable, ctx):
    async def handle(request: Request) -> JSONResponse:
        if getattr(request.state, "token_kind", None) != OWNER:
            return JSONResponse({"error": "only an owner token may use the hook routes"}, 403)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        try:
            if not isinstance(body, dict):
                raise _BadRequest("body must be a JSON object")
            args = parse(body)
        except _BadRequest as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            return JSONResponse(await anyio.to_thread.run_sync(lambda: run(ctx, args)))
        except Exception:  # noqa: BLE001 - the server never crashes on a hook call
            logger.exception("hook route %s failed", run.__name__)
            return JSONResponse({"error": "internal error"}, status_code=500)

    return handle


def hook_routes(ctx) -> dict[str, Callable]:
    """Path -> handler, bound to the server's context."""
    return {
        "/hook/recall": _endpoint(_parse_recall, _run_recall, ctx),
        "/hook/tripwires": _endpoint(_parse_tripwires, _run_tripwires, ctx),
        "/hook/trip": _endpoint(_parse_trip, _run_trip, ctx),
        "/hook/warmup": _endpoint(_parse_warmup, _run_warmup, ctx),
    }
