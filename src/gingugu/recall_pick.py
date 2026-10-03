"""The involuntary-recall pipeline, shared by the local hook and the brain.

``gingugu hook prompt`` runs it in-process against the local store; a remote
brain runs it for a client's hook (``serve_hooks``). Sharing one function is the
point: the same prompt must pick the same memories wherever the brain lives.
"""

from __future__ import annotations

from pathlib import Path

from .recall_gate import Candidate, GateConfig, select


def pick(
    db_path: Path,
    provider,
    cleaned: str,
    namespaces: list[str],
    cfg: GateConfig,
    suppressed: set[str],
    session_id: str,
) -> list[Candidate]:
    """Encode, sweep, lexically confirm, select, and log what was picked.

    ``cleaned`` is the affect-stripped prompt. Returns ``[]`` when the encoder
    is off or yields nothing. The log row is written whether or not anything
    was picked: a prompt that matched nothing is exactly what tuning needs.
    """
    from .prompt_hook import log_prompt
    from .recall_sweep import connect_readonly, lexical_matches, sweep

    query_vec = provider.encode(cleaned)
    if not query_vec:
        return []
    conn = connect_readonly(db_path)
    try:
        candidates = sweep(conn, list(query_vec), namespaces, config=cfg)
        lexical = lexical_matches(conn, cleaned, namespaces) if cfg.require_lexical else None
    finally:
        conn.close()
    picked = select(candidates, lexical_ids=lexical, config=cfg, suppressed=suppressed)
    log_prompt(db_path, session_id, cleaned, [c.id for c in picked], namespaces)
    return picked
