"""``code_xray`` action ``callers``: who calls one symbol, from the local symbol graph.

The answer is built where the checkout is, from the hook symbol cache, so the
brain is never asked and never sees a path. Shared by the stdio handler and the
proxy, like ``brief.answer``.
"""

from __future__ import annotations

from . import hook_common, local, symbols

MAX_LIMIT = 200
MAX_CANDIDATES = 20
CAVEAT = (
    "Static Python call graph, a lower bound: calls through untyped values and "
    "inherited methods are not resolved, so a caller can be missing but a listed "
    "one exists. Action 'blast' is the upper bound (every importer)."
)


def _candidates(sym: symbols.Symbols, rel: str, symbol: str) -> list[str]:
    names = sym.defs.get(rel, {})
    return sorted(q for q in names if q == symbol or q.endswith("." + symbol))[:MAX_CANDIDATES]


def answer(
    path: str | None,
    files,
    symbol: str | None,
    limit: int = 50,
    offset: int = 0,
    *,
    session_id: str | None = None,
) -> dict:
    """Direct callers and transitive reach of ``symbol`` in the one file named."""
    from ..config import load_config

    repo = local.root(path)
    named = local.file_list(files)
    if len(named) != 1:
        raise ValueError("files must name exactly one file for action 'callers'")
    rel = local.seeds(named, repo)[0]
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol is required for action 'callers'")
    symbol = symbol.strip()
    sym = hook_common.symbols(repo, load_config().db_path.parent / "hook-sessions", session_id)
    out = symbols.callers(
        sym, rel, symbol, limit=min(max(0, limit), MAX_LIMIT), offset=max(0, offset)
    )
    if out is None:
        return {
            "ok": False,
            "error": f"no definition '{symbol}' in {rel}",
            "candidates": _candidates(sym, rel, symbol),
        }
    return {
        "ok": True,
        **out,
        "coverage": {
            "resolved": sym.resolved,
            "external": sym.external,
            "unresolved": sym.unresolved,
        },
        "caveat": CAVEAT,
    }
