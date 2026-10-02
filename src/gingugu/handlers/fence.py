"""The tool-level half of scoped access: who is calling, and may they.

``request_grant`` finds the grant `gingugu serve`'s middleware attached to
the HTTP request this tool call arrived on, or for stdio the grant the process
was started with (``MEMORY_GRANT``, see ``stdio_grant``). ``refusal`` is the coarse policy
applied before a handler runs: tools whose blast radius is the whole brain
are closed to any scoped token outright. Everything finer - which namespace,
which memory - is enforced by the store's chokepoints (see ``grants``),
because only they see what a call actually touches.
"""

from __future__ import annotations

from ..grants import FULL, Grant

# Whole-brain tools: export and import move every namespace at once, the dream
# pass and its queue span the graph, and the credential vault has no namespace
# at all. A scoped token gets none of them (the secrets broker is its own item).
_CLOSED_TO_SCOPED = frozenset(
    {
        "memory_export",
        "memory_import",
        "memory_dream",
        "credential_store",
        "credential_get",
        "credential_list",
        "credential_delete",
    }
)


def stdio_grant(spec: str | None) -> Grant:
    """The grant a stdio server runs under, from ``MEMORY_GRANT``.

    Unset is the full grant: the local client has always owned the brain. A
    spec is how a warm minion's own server gets fenced. Anything that cannot be
    honoured raises, so the server never starts: a typo - or a set-but-empty
    value - must not fall back to the whole brain, and ``*=write`` is the owner
    wearing a disguise.
    """
    if spec is None:
        return FULL
    if not spec.strip():
        raise ValueError("MEMORY_GRANT is set but empty; unset it for full access")
    from ..serve_tokens import parse_grant_spec

    grant = Grant("MEMORY_GRANT", parse_grant_spec(spec))
    if grant.is_full:
        raise ValueError("MEMORY_GRANT may not grant '*=write'; unset it for full access")
    return grant


def request_grant(transport: str, stdio: Grant = FULL) -> Grant | None:
    """The grant for the tool call in progress, or None to refuse it.

    stdio has no network caller: its grant is the one the process was started
    with (``stdio``, full unless ``MEMORY_GRANT`` narrowed it). Under any other
    transport the grant is whatever the auth middleware attached to the HTTP
    request; a call that arrives without one - no request context, no
    request, no grant - is refused, never waved through. Fail closed even
    where the SDK today always supplies a context: that is the SDK's
    behaviour, not this server's guarantee.
    """
    if transport == "stdio":
        return stdio
    from mcp.server.lowlevel.server import request_ctx

    try:
        request = request_ctx.get().request
    except LookupError:
        return None
    state = getattr(request, "state", None)
    grant = getattr(state, "grant", None) if state is not None else None
    return grant if isinstance(grant, Grant) else None


def refusal(tool: str, grant: Grant, kwargs: dict) -> str | None:
    """Why a scoped token may not call ``tool`` at all, or None."""
    if grant.is_full:
        return None
    if tool in _CLOSED_TO_SCOPED:
        return f"{tool} is not available to a scoped token"
    if tool == "memory_namespaces" and kwargs.get("action", "list") != "list":
        return "memory_namespaces only allows action 'list' for a scoped token"
    return None
