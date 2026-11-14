"""``gingugu promote --to-crew``: a crew mate's gold, from its local brain to its crew.

The source is this machine's own brain file, read directly - no server to
stand up. The target is the crew link (``gingugu.crew``), on the link's
scoped token, into the ``crew`` namespace. The locked filter in ``promote``
applies, and the mate's self stays home: its persona namespace is refused as
a source, and a memory tagged with its persona name or a ``--skip-tag`` is
held back. Promotion is idempotent - the target's provenance stamps say what
already went up.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .remote import RemoteTarget

CREW_NAMESPACE = "crew"


def local_candidates(db_path: Path, namespace: str) -> list[dict]:
    """Every live memory in ``namespace`` of the brain at ``db_path``, as exported."""
    from .portability import export_data

    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id FROM namespaces WHERE name = ?", (namespace,)).fetchone()
        if row is None:
            raise ValueError(f"no namespace {namespace!r} in {db_path}")
        return export_data(conn, namespace_id=row["id"], include_deprecated=False)["memories"]
    finally:
        conn.close()


async def promote_to_crew(
    *,
    db_path: Path,
    source_namespace: str,
    target: RemoteTarget,
    token: str,
    persona: str,
    skip_tags: list[str] | None = None,
    instance: str | None = None,
    dry_run: bool,
) -> dict:
    """Filter ``source_namespace`` of the local brain and push its gold to the crew."""
    from .promote import is_promotable, push

    if source_namespace == persona:
        raise ValueError(f"{source_namespace!r} is your persona namespace - your self stays home")
    held = frozenset(t.lower() for t in [persona, *(skip_tags or [])])
    candidates = local_candidates(db_path, source_namespace)
    return await push(
        candidates,
        [m for m in candidates if is_promotable(m, held)],
        source_namespace=source_namespace,
        target_url=f"{target.url}/mcp",
        target_token=token,
        target_namespace=CREW_NAMESPACE,
        contributor=persona,
        instance=instance or persona,
        dry_run=dry_run,
    )
