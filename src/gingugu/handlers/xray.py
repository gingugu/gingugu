"""The ``code_xray`` tool: a repository's import graph, ranked, and what a change reaches.

Storage and math live in ``xray.store``/``xray.rank``; reading the checkout in
``xray.local``/``xray.extract``. This is the MCP surface, and the one place
that decides where a snapshot comes from:

- a ``fingerprint`` names one (the proxy sends it, having read the disk itself);
- on stdio, the caller's checkout at ``path`` is fingerprinted and, when the
  brain has no snapshot of exactly that content, extracted and ingested first -
  the agent never has to ask for a refresh;
- otherwise the namespace's most recently used snapshot, marked
  ``freshness: unknown`` because nothing compared it with any working tree.

Under `gingugu serve` the tool never reads a path: that would be the host's disk.
"""

from __future__ import annotations

import logging

from .. import grants
from ..xray import brief, extract, local, store
from . import ServerContext
from .helpers import _err

logger = logging.getLogger(__name__)

_ACTIONS = ("blast", "overview", "refresh", "ingest", "brief")
_CAVEAT = brief.CAVEAT
_NO_DISK = (
    "under `gingugu serve` the brain cannot read your checkout; call through the "
    "gingugu proxy, pass a fingerprint, or send action='ingest' with a payload"
)


class _Missing(Exception):
    pass


def register(mcp, ctx: ServerContext) -> None:
    def _local(ns_id: str, path: str | None) -> tuple[dict, object]:
        repo = local.root(path)
        meta = store.find(ctx.conn, ns_id, extract.fingerprint(repo))
        if meta is None:
            x = extract.extract(repo)
            store.ingest(ctx.conn, ns_id, x.payload())
            meta = store.find(ctx.conn, ns_id, x.fingerprint)
        return meta, repo

    def _snapshot(ns_id: str, path, fingerprint) -> tuple[dict, object, str]:
        if path and ctx.transport != "stdio":
            raise ValueError(_NO_DISK)
        if fingerprint:
            meta = store.find(ctx.conn, ns_id, fingerprint)
            if meta is None:
                raise _Missing(fingerprint)
            # The snapshot of exactly that content; no working tree was looked at.
            return meta, None, "exact"
        if ctx.transport == "stdio":
            meta, repo = _local(ns_id, path)
            return meta, repo, "current"
        meta = store.find(ctx.conn, ns_id)
        if meta is None:
            raise _Missing(None)
        return meta, None, "unknown"

    @mcp.tool()
    def code_xray(
        action: str,
        namespace: str | None = None,
        files: str | list[str] | None = None,
        path: str | None = None,
        fingerprint: str | None = None,
        payload: dict | None = None,
        replace: bool = False,
        limit: int = 50,
        offset: int = 0,
        top: int = 20,
        range: str | None = None,  # noqa: A002 - the tool's argument name
    ) -> dict:
        """X-ray a repository's import graph (Python, TypeScript/JavaScript, Go): what a
        change reaches, and what the code leans on. Use before editing a file to see its
        dependents and the tests to run.

        Actions: "blast" (files: the files you are changing, comma-separated or a list;
        returns every transitive importer, split into code and candidate tests, with hop
        count and rank, paged by limit/offset), "overview" (top: the most load-bearing
        files by directed PageRank, plus files no test or entry point reaches), "refresh"
        (rebuild now, replacing a stored snapshot whose graph differs), "ingest" (payload:
        a client-extracted graph; ``replace`` as refresh does), "brief" (a handoff: the changed
        files by rank plus their combined blast, for ``range`` such as main..HEAD, the named
        files, or the working tree; computed locally, nothing stored).

        ``path`` is any directory in the git checkout (default: the working directory).
        The snapshot is rebuilt automatically when the checkout's source content changed,
        so results match the working tree (``freshness: current``); a ``fingerprint``
        names one snapshot exactly (``exact``); with neither, the latest (``unknown``).
        ``namespace`` scopes it like a memory.
        Results are import-based estimates; see ``caveat`` in each answer."""
        try:
            if action not in _ACTIONS:
                return _err(f"unknown action {action!r}; valid actions: {', '.join(_ACTIONS)}")
            if action == "brief":
                if ctx.transport != "stdio":
                    return _err(_NO_DISK)
                return brief.answer(path, range, files, limit)
            ns_id = ctx.namespaces.get_or_create(ctx.namespaces.resolve_name(namespace)).id
            if action == "ingest":
                if payload is None:
                    return _err("payload is required for action 'ingest'")
                out = store.ingest(ctx.conn, ns_id, payload, replace=bool(replace))
                ctx.conn.commit()
                return {"ok": True, **out}
            if action == "refresh":
                if ctx.transport != "stdio":
                    return _err(_NO_DISK)
                x = extract.extract(local.root(path))
                out = store.ingest(ctx.conn, ns_id, x.payload(), replace=True)
                ctx.conn.commit()
                return {"ok": True, **out, "snapshot": store.find(ctx.conn, ns_id, x.fingerprint)}

            meta, repo, freshness = _snapshot(ns_id, path, fingerprint)
            ctx.conn.commit()
            if action == "overview":
                out = store.overview(
                    ctx.conn,
                    meta["snapshot_id"],
                    top=top,
                    limit=max(0, limit),
                    offset=max(0, offset),
                )
            else:
                named = local.file_list(files)
                if not named:
                    return _err("files is required for action 'blast'")
                seeds = local.seeds(named, repo) if repo is not None else named
                out = store.blast(
                    ctx.conn, meta["snapshot_id"], seeds, limit=max(0, limit), offset=max(0, offset)
                )
            return {"ok": True, "freshness": freshness, **out, "caveat": _CAVEAT}
        except _Missing as exc:
            ctx.conn.rollback()
            missing = str(exc) if exc.args and exc.args[0] else None
            return {
                "ok": False,
                "missing_snapshot": True,
                "fingerprint": missing,
                "error": "no X-ray snapshot of that content; refresh or ingest one first",
            }
        except (extract.XrayError, store.XrayStoreError, ValueError, grants.AccessDenied) as exc:
            ctx.conn.rollback()
            return _err(str(exc))
        except Exception as exc:
            ctx.conn.rollback()
            logger.exception("code_xray failed")
            return _err(f"code_xray failed: {exc}")
