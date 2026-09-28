"""Namespace scope for the lookup read surfaces: resolve, then widen on empty.

A lookup ("find me X") that comes back empty in the namespaces it was scoped
to is not an answer, it is a miss - the thing may well live in another repo's
namespace, and "look somewhere else" is not an event the caller can notice
from inside. So a scoped lookup that returns nothing reruns once across every
namespace and reports ``widened_from``, keeping every other filter.

An unconfigured server (no MEMORY_NAMESPACE, no MEMORY_NAMESPACE_PATH) has no
current project at all, so an omitted namespace means every namespace rather
than the ``default`` fallback, which is only ever right for writes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..models import Memory
from . import ServerContext
from .helpers import _resolve_namespaces, _split_csv

NamespaceArg = str | list[str] | None


@dataclass(frozen=True)
class ReadScope:
    """The namespaces a read covers. No ``names`` means every namespace."""

    names: list[str]
    ns_ids: list[str]

    @property
    def is_all(self) -> bool:
        return not self.names


def read_scope(
    ctx: ServerContext, namespace: str | None, *, use_config: bool
) -> tuple[ReadScope | None, dict | None]:
    """Resolve a read's ``namespace`` argument into a scope, or an error.

    Explicit names must exist (reads never mint namespaces). With none given,
    ``use_config`` falls back to the configured namespace - which may not have
    been created yet, and then scopes to nothing so the widen still runs.
    """
    requested = list(dict.fromkeys(_split_csv(namespace)))
    if requested:
        resolved, error = _resolve_namespaces(ctx, requested)
        if error is not None:
            return None, error
        return ReadScope(requested, [ns.id for ns in resolved.values()]), None
    configured = ctx.config.resolved_namespace if use_config else None
    if not configured:
        return ReadScope([], []), None
    ns = ctx.namespaces.get(configured)
    return ReadScope([configured], [ns.id] if ns else []), None


def run_widening(
    scope: ReadScope,
    run: Callable[[NamespaceArg], list[Memory]],
    *,
    widen: bool = True,
) -> tuple[list[Memory], list[str] | None]:
    """Run ``run`` in scope; on an empty scoped result, rerun across everything.

    Returns ``(results, widened_from)``; ``widened_from`` is None unless the
    rerun happened, and is reported even when the rerun also finds nothing -
    "looked everywhere, nothing" is a different answer from "nothing here".
    """
    if scope.is_all:
        return run(None), None
    ids = scope.ns_ids
    results = run(ids[0] if len(ids) == 1 else ids) if ids else []
    if results or not widen:
        return results, None
    return run(None), list(scope.names)
