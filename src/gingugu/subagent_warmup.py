"""The minion warm-up ranking, shared by ``gingugu hook subagent`` and the brain.

Split out of ``subagent_hook`` so a remote brain can run the same read-only
ranking for a client's hook (``serve_hooks``) without importing the hook's
stdin/stash machinery.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _render(agent_type: str, grant, rows: list[tuple[str, dict]]) -> str:
    reads = sorted(n for n, lvl in grant.namespaces.items() if lvl)
    writes = sorted(n for n, lvl in grant.namespaces.items() if lvl == "write")
    lines = [
        f"gingugu warm-up for minion `{agent_type}` - read: {', '.join(reads)}; "
        f"write: {', '.join(writes) or 'nothing'}.",
        "These memories ranked most relevant to your task. Your gingugu server is "
        "fenced to the same grant: pull a full body with memory_recall, and store "
        "findings only in a namespace you can write"
        + (f" ({', '.join(writes)})." if writes else "."),
        "",
    ]
    for ns, summary in rows:
        body = summary.get("summary") or ""
        lines.append(f"- [{summary.get('type')}] {summary.get('title')} ({ns}) - {body}")
    return "\n".join(lines)


def _in_scope(grant, name: str, scope: list[str] | None) -> bool:
    """Whether the warm-up loads ``name``. A wildcard read belongs to a global
    agent that works in whatever repo it is spawned in: it warms from crow and
    that repo's namespace, never the whole brain. Explicit names load as is."""
    if not grant.can_read(name):
        return False
    return grant.namespaces.get(name) is not None or scope is None or name in scope


def warmup(
    db_path: Path,
    spec: str,
    task_hint: str | None,
    *,
    agent_type: str,
    embedder=None,
    cwd: str | None = None,
    scope: list[str] | None = None,
):
    """The context to inject for a minion fenced by ``spec``, or None.

    ``scope`` names the namespaces a wildcard read may load. A caller on another
    machine (the brain serving a remote hook) sends the client's own list, since
    only the client knows which repo it is in; ``cwd`` derives it locally.
    """
    from . import grants
    from .config import load_config
    from .context import build_context
    from .handlers.fence import stdio_grant
    from .handlers.summaries import _compact_summary
    from .recall_sweep import connect_readonly

    if scope is None and cwd is not None:
        from .prompt_hook import namespaces_for

        scope = namespaces_for(cwd)
    try:
        grant = stdio_grant(spec)
    except ValueError:
        return None
    if grant.is_full:
        return None
    app = load_config()
    conn = connect_readonly(db_path)
    # The store's readers index rows by column name, as the server's own
    # connection does.
    conn.row_factory = sqlite3.Row
    try:
        with grants.bind(grant, conn):
            names = conn.execute("SELECT id, name FROM namespaces ORDER BY name").fetchall()
            name_of = {ns_id: name for ns_id, name in names}
            seen: set[str] = set()
            rows: list[tuple[str, dict]] = []
            for ns_id, name in names:
                if not _in_scope(grant, name, scope):
                    continue
                for mem in build_context(
                    conn,
                    namespace_id=ns_id,
                    task_hint=task_hint,
                    limit=app.auto_context_limit,
                    weights=app.weights,
                    decay_lambda=app.decay_lambda,
                    embedder=embedder,
                ):
                    if mem.id in seen or not grants.can_read_id(mem.namespace_id):
                        continue
                    seen.add(mem.id)
                    rows.append((name_of.get(mem.namespace_id, name), _compact_summary(mem)))
    finally:
        conn.close()
    return _render(agent_type, grant, rows) if rows else None
